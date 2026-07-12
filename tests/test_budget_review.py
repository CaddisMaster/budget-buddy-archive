"""v10.7 tests — AI Budget Review (facts builder + scan + apply + app.ai).

No real Anthropic API calls: the single network seam, app.ai._call_budget_model,
is monkeypatched to feed canned reviews, so the real normalizer (with its clamp),
the facts builder, the no-op filter, and the write-side guards all run end-to-end
while CI (no key) stays offline. The model proposes the amounts here — so the
normalizer's clamp/ownership resolution and the apply path's IDOR guard are the
security boundary under test.
"""
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import app.ai as ai
from app.ai import propose_budgets, _normalize_budget_proposals, ParseError
from app.blueprints.budgets import compute_budget_review_facts
from conftest import (
    create_budget, create_category, create_transaction, fetch_budget_by_category,
)

HX = {"HX-Request": "true"}


# --- helpers ----------------------------------------------------------------

def _prop(category, amount, reason="Based on typical months"):
    return SimpleNamespace(category=category, amount=amount, reason=reason)


def _review(summary, *proposals):
    return SimpleNamespace(summary=summary, proposals=list(proposals))


def _fact(name, avg=None, current=None, totals=None):
    totals = totals or []
    return {
        "name": name,
        "monthly_totals": [{"month": m, "spent": s} for m, s in totals],
        "months_with_spend": len(totals),
        "avg_monthly": avg,
        "current_budget": current,
        "this_month_spent": 0.0,
        "overrun_months": 0,
    }


def _by_name(*facts):
    return {f["name"].strip().lower(): f for f in facts}


def _entry(facts, name):
    return next((c for c in facts["categories"] if c["name"] == name), None)


@pytest.fixture
def ai_on(monkeypatch):
    """ai_enabled() reads ANTHROPIC_API_KEY at runtime — set it so the review
    routes (which 404 when AI is off) are reachable in tests."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


# --- pure normalizer ---------------------------------------------------------

def test_normalize_resolves_name_and_passes_in_range_amount():
    cats = [(10, "Groceries"), (11, "Gas")]
    facts = _by_name(_fact("Groceries", avg=100, totals=[("2026-05", 100.0)]))
    parsed = _review("ok", _prop("groceries", 150))       # case-insensitive
    out = _normalize_budget_proposals(parsed, cats, facts)
    assert out == [{"category_id": 10, "category_name": "Groceries",
                    "amount": 150, "reason": "Based on typical months"}]


def test_normalize_drops_unknown_category():
    cats = [(10, "Groceries")]
    facts = _by_name(_fact("Groceries", avg=100))
    parsed = _review("ok", _prop("Crypto", 100))          # not the user's category
    assert _normalize_budget_proposals(parsed, cats, facts) == []


def test_normalize_drops_duplicate_first_wins():
    cats = [(10, "Groceries")]
    facts = _by_name(_fact("Groceries", avg=100, totals=[("2026-05", 100.0)]))
    parsed = _review("ok", _prop("Groceries", 120), _prop("Groceries", 60))
    out = _normalize_budget_proposals(parsed, cats, facts)
    assert [o["amount"] for o in out] == [120]


def test_normalize_snaps_out_of_range_amounts():
    # avg=100, worst month 180 -> range [50, 200]; snap, don't drop.
    cats = [(10, "Groceries"), (11, "Gas")]
    facts = _by_name(_fact("Groceries", avg=100, totals=[("2026-05", 180.0)]),
                     _fact("Gas", avg=100, totals=[("2026-05", 180.0)]))
    parsed = _review("ok", _prop("Groceries", 10), _prop("Gas", 900))
    out = _normalize_budget_proposals(parsed, cats, facts)
    assert [o["amount"] for o in out] == [50, 200]


def test_normalize_range_widens_to_worst_observed_month():
    # A 350 spike month beats the 2x-avg ceiling, so budgeting for it is allowed.
    cats = [(10, "Groceries")]
    facts = _by_name(_fact("Groceries", avg=100, totals=[("2026-05", 350.0)]))
    parsed = _review("ok", _prop("Groceries", 300))
    out = _normalize_budget_proposals(parsed, cats, facts)
    assert out[0]["amount"] == 300


def test_normalize_drops_garbage_amounts():
    cats = [(10, "Groceries"), (11, "Gas")]
    facts = _by_name(_fact("Groceries", avg=100), _fact("Gas", avg=100))
    parsed = _review("ok", _prop("Groceries", "abc"), _prop("Gas", -5))
    assert _normalize_budget_proposals(parsed, cats, facts) == []


def test_normalize_reason_default_and_truncation():
    cats = [(10, "Groceries"), (11, "Gas")]
    facts = _by_name(_fact("Groceries", avg=100), _fact("Gas", avg=100))
    parsed = _review("ok", _prop("Groceries", 100, reason="  "),
                     _prop("Gas", 100, reason="x" * 300))
    out = _normalize_budget_proposals(parsed, cats, facts)
    assert out[0]["reason"] == "Based on your recent spending"
    assert len(out[1]["reason"]) == 200


def test_normalize_drops_no_basis_and_unasked_categories():
    cats = [(10, "Groceries"), (11, "Gas")]
    # Groceries has no avg AND no saved budget; Gas wasn't in the facts at all.
    facts = _by_name(_fact("Groceries", avg=None, current=None))
    parsed = _review("ok", _prop("Groceries", 100), _prop("Gas", 100))
    assert _normalize_budget_proposals(parsed, cats, facts) == []


# --- propose_budgets ----------------------------------------------------------

def test_propose_budgets_empty_candidates_short_circuits(monkeypatch):
    # No key needed and no model call when there's nothing to review.
    monkeypatch.setattr(ai, "_call_budget_model",
                        lambda *a, **k: pytest.fail("should not call the model"))
    out = propose_budgets({"categories": []}, [(10, "Groceries")])
    assert out == {"summary": "", "proposals": []}


def test_propose_budgets_happy_path_and_empty_summary(monkeypatch, ai_on):
    cats = [(10, "Groceries")]
    facts = {"categories": [_fact("Groceries", avg=100,
                                  totals=[("2026-05", 100.0)])]}
    monkeypatch.setattr(ai, "_call_budget_model",
                        lambda *a, **k: _review("Looks solid", _prop("Groceries", 120)))
    out = propose_budgets(facts, cats)
    assert out["summary"] == "Looks solid"
    assert out["proposals"][0]["category_id"] == 10

    monkeypatch.setattr(ai, "_call_budget_model",
                        lambda *a, **k: _review("   "))
    with pytest.raises(ParseError):
        propose_budgets(facts, cats)


# --- facts builder ------------------------------------------------------------

def test_facts_figures_and_overruns(users):
    a = users["a"]
    cid = create_category(a["id"], "food-A")
    create_transaction(a["id"], a["account_id"], 60, date.today(), category_id=cid)
    prev = date.today() - timedelta(days=35)
    create_transaction(a["id"], a["account_id"], 90, prev, category_id=cid)
    create_transaction(a["id"], a["account_id"], 30, prev, category_id=cid)
    create_budget(a["id"], cid, 100)

    entry = _entry(compute_budget_review_facts(a["id"]), "food-A")
    assert entry is not None
    assert entry["months_with_spend"] == 2
    assert entry["avg_monthly"] == pytest.approx(90.0)      # (60 + 120) / 2
    assert entry["current_budget"] == 100
    assert entry["this_month_spent"] == pytest.approx(60.0)
    assert entry["overrun_months"] == 1                     # only the 120 month


def test_facts_dormant_saved_budget_is_candidate_ghost_is_not(users):
    a = users["a"]
    dormant = create_category(a["id"], "dormant-A")
    create_budget(a["id"], dormant, 80)                     # saved, no spend
    create_category(a["id"], "ghost-A")                     # no spend, no budget

    facts = compute_budget_review_facts(a["id"])
    entry = _entry(facts, "dormant-A")
    assert entry is not None
    assert entry["months_with_spend"] == 0
    assert entry["avg_monthly"] is None
    assert entry["current_budget"] == 80
    assert _entry(facts, "ghost-A") is None


def test_facts_exclude_income_adjustments_and_transfers(users):
    a = users["a"]
    cid = create_category(a["id"], "mix-A")
    create_transaction(a["id"], a["account_id"], 50, date.today(), category_id=cid)
    create_transaction(a["id"], a["account_id"], 500, date.today(), category_id=cid,
                       transaction_type="income")
    create_transaction(a["id"], a["account_id"], 70, date.today(), category_id=cid,
                       is_adjustment=True)
    entry = _entry(compute_budget_review_facts(a["id"]), "mix-A")
    assert entry["this_month_spent"] == pytest.approx(50.0)
    assert entry["avg_monthly"] == pytest.approx(50.0)


def test_facts_exclude_income_kind_categories(users):
    # v10.12: an income-kind category is never a review candidate — not for
    # stray expense-typed spend, and not for a lingering saved budget.
    a = users["a"]
    inc = create_category(a["id"], "salary-A", kind="income")
    create_transaction(a["id"], a["account_id"], 75, date.today(), category_id=inc)
    create_budget(a["id"], inc, 90)
    assert _entry(compute_budget_review_facts(a["id"]), "salary-A") is None


def test_facts_are_user_scoped(users):
    a = users["a"]
    facts = compute_budget_review_facts(a["id"])
    names = [c["name"] for c in facts["categories"]]
    assert "cat-A" in names                                 # A's seeded spend
    assert "cat-B" not in names                             # never B's


# --- scan endpoint ------------------------------------------------------------

def test_scan_requires_login_and_key(anon_client, client_a, monkeypatch):
    assert anon_client.post("/budgets/review/scan").status_code == 302
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # AI off -> hidden
    assert client_a.post("/budgets/review/scan", headers=HX).status_code == 404


def test_scan_renders_review_fragment(monkeypatch, ai_on, users, client_a):
    a = users["a"]
    monkeypatch.setattr(ai, "_call_budget_model",
                        lambda *args, **k: _review("Room to trim",
                                                   _prop("cat-A", 50, "Right-size it")))
    resp = client_a.post("/budgets/review/scan", headers=HX)
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "<html" not in body                              # fragment, not a page
    assert "Room to trim" in body                           # narrated summary
    assert f'name="amount_{a["category_id"]}"' in body      # editable proposal
    assert "Right-size it" in body


def test_scan_filters_noop_proposals(monkeypatch, ai_on, users, client_a):
    # Proposal equals the saved amount -> nothing to confirm -> toast, no panel.
    a = users["a"]
    create_budget(a["id"], a["category_id"], 50)
    monkeypatch.setattr(ai, "_call_budget_model",
                        lambda *args, **k: _review("All good", _prop("cat-A", 50)))
    resp = client_a.post("/budgets/review/scan", headers=HX)
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == ""
    assert resp.headers.get("HX-Reswap") == "none"          # panel untouched
    assert "right-sized" in resp.headers.get("HX-Trigger", "")


def test_scan_graceful_when_model_fails(monkeypatch, ai_on, users, client_a):
    def _boom(*args, **k):
        raise ParseError("nope")
    monkeypatch.setattr(ai, "_call_budget_model", _boom)
    resp = client_a.post("/budgets/review/scan", headers=HX)
    assert resp.status_code == 200                          # degrades, never 500
    assert resp.headers.get("HX-Reswap") == "none"
    assert resp.headers.get("HX-Trigger")                   # error toast fired


# --- apply endpoint (the write-side security boundary) --------------------------

def test_apply_upserts_checked_rows(ai_on, users, client_a):
    a = users["a"]
    resp = client_a.post("/budgets/review/apply", headers=HX, data={
        "category_id": str(a["category_id"]),
        f'amount_{a["category_id"]}': "123"})
    assert resp.status_code == 200
    assert 'id="budget-rows"' in resp.get_data(as_text=True)  # whole-table refresh
    row = fetch_budget_by_category(a["id"], a["category_id"])
    assert row is not None and float(row[1]) == 123.0

    # Second apply hits the ON CONFLICT update path.
    client_a.post("/budgets/review/apply", headers=HX, data={
        "category_id": str(a["category_id"]),
        f'amount_{a["category_id"]}': "200"})
    assert float(fetch_budget_by_category(a["id"], a["category_id"])[1]) == 200.0


def test_apply_skips_unchecked_rows(ai_on, users, client_a):
    a = users["a"]
    # amount posted but the checkbox (category_id) absent -> nothing written.
    client_a.post("/budgets/review/apply", headers=HX, data={
        f'amount_{a["category_id"]}': "123"})
    assert fetch_budget_by_category(a["id"], a["category_id"]) is None


def test_apply_rejects_other_users_category(ai_on, users, client_a):
    a, b = users["a"], users["b"]
    client_a.post("/budgets/review/apply", headers=HX, data={
        "category_id": str(b["category_id"]),                # B's category
        f'amount_{b["category_id"]}': "123"})
    assert fetch_budget_by_category(a["id"], b["category_id"]) is None
    assert fetch_budget_by_category(b["id"], b["category_id"]) is None


def test_scan_drops_proposals_for_income_kind(monkeypatch, ai_on, users, client_a):
    # The scan's category list is expense-kind only, so a proposal naming an
    # income-kind category resolves to nothing -> toast, no panel.
    a = users["a"]
    create_category(a["id"], "salary-A", kind="income")
    monkeypatch.setattr(ai, "_call_budget_model",
                        lambda *args, **k: _review("Trim", _prop("salary-A", 50)))
    resp = client_a.post("/budgets/review/scan", headers=HX)
    assert resp.status_code == 200
    assert resp.headers.get("HX-Reswap") == "none"


def test_apply_rejects_income_kind_category(ai_on, users, client_a):
    a = users["a"]
    inc = create_category(a["id"], "salary-A", kind="income")
    client_a.post("/budgets/review/apply", headers=HX, data={
        "category_id": str(inc), f"amount_{inc}": "123"})
    assert fetch_budget_by_category(a["id"], inc) is None


def test_apply_skips_invalid_amounts(ai_on, users, client_a):
    a = users["a"]
    resp = client_a.post("/budgets/review/apply", headers=HX, data={
        "category_id": str(a["category_id"]),
        f'amount_{a["category_id"]}': "0"})
    assert fetch_budget_by_category(a["id"], a["category_id"]) is None
    assert "Nothing applied" in resp.headers.get("HX-Trigger", "")


# --- page ----------------------------------------------------------------------

def test_budgets_page_shows_review_banner_only_with_key(ai_on, users, client_a,
                                                        monkeypatch):
    body = client_a.get("/budgets").get_data(as_text=True)
    assert "/budgets/review/scan" in body
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    body = client_a.get("/budgets").get_data(as_text=True)
    assert "/budgets/review/scan" not in body
