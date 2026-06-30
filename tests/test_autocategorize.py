"""v10.5 tests — Auto-Categorize & Cleanup (banner + scan + apply + app.ai).

No real Anthropic API calls: the single network seam, app.ai._call_categorize_model,
is monkeypatched to feed canned batch suggestions, so the real normalizer, the
candidate query, the scan filter, and the write-side guards all run end-to-end
while CI (no key) stays offline. The model only PROPOSES; the app picks the
candidates and owns the write — so the isolation/IDOR tests live on the apply
path (the security boundary), like the rest of the suite.
"""
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import app.ai as ai
from app.ai import classify_transactions, _normalize_suggestions, ParseError
import app.blueprints.transactions as txns
from app.db import get_db_connection
from conftest import create_transaction, create_category

HX = {"HX-Request": "true"}


# --- helpers ----------------------------------------------------------------

def _sugg(id, category, confidence="high"):
    return SimpleNamespace(id=id, category=category, confidence=confidence)


def _batch(*suggestions):
    return SimpleNamespace(suggestions=list(suggestions))


def _fetch_category_id(transaction_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT category_id FROM transactions WHERE id = %s", (transaction_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


@pytest.fixture
def ai_on(monkeypatch):
    """ai_enabled() reads ANTHROPIC_API_KEY at runtime — set it so the cleanup
    routes (which 404 when AI is off) are reachable in tests."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


# --- pure normalizer --------------------------------------------------------

def test_normalize_resolves_name_to_owned_id():
    cats = [(10, "Groceries"), (11, "Gas")]
    parsed = _batch(_sugg(1, "groceries", "high"))  # case-insensitive
    out = _normalize_suggestions(parsed, cats, {1})
    assert out == [{"id": 1, "category_id": 10, "category_name": "Groceries",
                    "confidence": "high"}]


def test_normalize_drops_unknown_category():
    cats = [(10, "Groceries")]
    parsed = _batch(_sugg(1, "Crypto", "high"))     # not the user's category
    assert _normalize_suggestions(parsed, cats, {1}) == []


def test_normalize_drops_ids_outside_batch_and_defaults_confidence():
    cats = [(10, "Groceries")]
    parsed = _batch(_sugg(99, "Groceries", "high"),   # id we never asked about
                    _sugg(1, "Groceries", "bogus"))   # bad confidence -> low
    out = _normalize_suggestions(parsed, cats, {1})
    assert [o["id"] for o in out] == [1]
    assert out[0]["confidence"] == "low"


def test_classify_transactions_uses_seam(monkeypatch, ai_on):
    cats = [(10, "Groceries")]
    monkeypatch.setattr(ai, "_call_categorize_model",
                        lambda *a, **k: _batch(_sugg(1, "Groceries", "high")))
    out = classify_transactions([{"id": 1, "description": "Safeway"}], cats)
    assert out[0]["category_id"] == 10


def test_classify_empty_rows_short_circuits(monkeypatch):
    # No key needed and no model call when there's nothing to classify.
    monkeypatch.setattr(ai, "_call_categorize_model",
                        lambda *a, **k: pytest.fail("should not call the model"))
    assert classify_transactions([], [(10, "Groceries")]) == []


# --- candidate query + count -----------------------------------------------

def test_count_uncategorized_excludes_transfers_and_adjustments(users):
    a = users["a"]
    create_transaction(a["id"], a["account_id"], 5, date.today(), category_id=None)
    create_transaction(a["id"], a["account_id"], 6, date.today(), category_id=None,
                       is_adjustment=True)  # excluded
    # seed gave A one CATEGORIZED txn, so only the plain uncategorized one counts.
    assert txns.count_uncategorized(a["id"]) == 1


def test_count_and_candidates_exclude_income(users):
    # Income rows can never get a suggestion (categories are expense categories),
    # so they're scoped out of both the banner count and the scan candidates.
    a = users["a"]
    create_transaction(a["id"], a["account_id"], 5, date.today(), category_id=None,
                       transaction_type="income")
    exp = create_transaction(a["id"], a["account_id"], 6, date.today(), category_id=None,
                             transaction_type="expense")
    assert txns.count_uncategorized(a["id"]) == 1          # only the expense
    ids = [r["id"] for r in txns._load_cleanup_candidates(a["id"])]
    assert exp in ids
    assert all(r["type"] == "expense" for r in txns._load_cleanup_candidates(a["id"]))


def test_candidates_are_user_scoped_and_uncategorized_first(users):
    a, b = users["a"], users["b"]
    a_un = create_transaction(a["id"], a["account_id"], 5, date.today(), category_id=None)
    b_un = create_transaction(b["id"], b["account_id"], 7, date.today(), category_id=None)
    rows = txns._load_cleanup_candidates(a["id"])
    ids = [r["id"] for r in rows]
    assert a_un in ids
    assert b_un not in ids                 # A never sees B's rows
    a_row = next(r for r in rows if r["id"] == a_un)
    assert a_row["current_category"] is None


def test_candidates_respect_cap(users):
    a = users["a"]
    for _ in range(5):
        create_transaction(a["id"], a["account_id"], 1, date.today(), category_id=None)
    assert len(txns._load_cleanup_candidates(a["id"], cap=3)) == 3


# --- scan endpoint ----------------------------------------------------------

def test_scan_requires_login(anon_client):
    assert anon_client.post("/transactions/cleanup/scan").status_code == 302


def test_scan_surfaces_uncategorized_any_confidence(monkeypatch, ai_on, users, client_a):
    a = users["a"]
    u1 = create_transaction(a["id"], a["account_id"], 5, date.today(), category_id=None)
    monkeypatch.setattr(ai, "_call_categorize_model",
                        lambda *args, **k: _batch(_sugg(u1, "cat-A", "low")))
    resp = client_a.post("/transactions/cleanup/scan", headers=HX)
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "<html" not in body                      # fragment, not a full page
    assert f'value="{u1}"' in body                  # the row is in the review list
    assert "cat-A" in body


def test_scan_skips_low_confidence_recategorize(monkeypatch, ai_on, users, client_a):
    # The seeded txn is already cat-A; a LOW-confidence move to cat2 must NOT surface.
    a = users["a"]
    create_category(a["id"], "cat2-A")
    monkeypatch.setattr(ai, "_call_categorize_model",
                        lambda *args, **k: _batch(
                            _sugg(a["transaction_id"], "cat2-A", "low")))
    resp = client_a.post("/transactions/cleanup/scan", headers=HX)
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert f'value="{a["transaction_id"]}"' not in body


def test_scan_graceful_when_model_fails(monkeypatch, ai_on, users, client_a):
    a = users["a"]
    create_transaction(a["id"], a["account_id"], 5, date.today(), category_id=None)
    def _boom(*args, **k):
        raise ParseError("nope")
    monkeypatch.setattr(ai, "_call_categorize_model", _boom)
    resp = client_a.post("/transactions/cleanup/scan", headers=HX)
    assert resp.status_code == 200                   # degrades, never 500
    assert resp.headers.get("HX-Trigger")            # error toast fired


# --- apply endpoint (the write-side security boundary) ----------------------

def test_apply_categorizes_checked_row(ai_on, users, client_a):
    a = users["a"]
    u1 = create_transaction(a["id"], a["account_id"], 5, date.today(), category_id=None)
    resp = client_a.post("/transactions/cleanup/apply", headers=HX, data={
        "row_id": str(u1), f"category_{u1}": str(a["category_id"])})
    assert resp.status_code == 200
    assert _fetch_category_id(u1) == a["category_id"]


def test_apply_rejects_other_users_category(ai_on, users, client_a):
    a, b = users["a"], users["b"]
    u1 = create_transaction(a["id"], a["account_id"], 5, date.today(), category_id=None)
    client_a.post("/transactions/cleanup/apply", headers=HX, data={
        "row_id": str(u1), f"category_{u1}": str(b["category_id"])})  # B's category
    assert _fetch_category_id(u1) is None            # write-side IDOR blocked


def test_apply_cannot_touch_other_users_transaction(ai_on, users, client_a):
    a, b = users["a"], users["b"]
    b_txn = b["transaction_id"]                       # already cat-B
    client_a.post("/transactions/cleanup/apply", headers=HX, data={
        "row_id": str(b_txn), f"category_{b_txn}": str(a["category_id"])})
    assert _fetch_category_id(b_txn) == b["category_id"]  # B's row unchanged


# --- banner -----------------------------------------------------------------

def test_history_banner_shows_count(ai_on, users, client_a):
    a = users["a"]
    create_transaction(a["id"], a["account_id"], 5, date.today(), category_id=None)
    body = client_a.get("/transactions").get_data(as_text=True)
    assert "uncategorized transaction" in body
    assert "/transactions/cleanup/scan" in body
