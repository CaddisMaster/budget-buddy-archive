"""v10.1 tests — monthly "Insight" digest (/insights/generate + app.ai).

No real Anthropic API calls: the single network seam, app.ai._call_insight_model,
is monkeypatched to return a canned _Insight, so the route, the deterministic
fact-builder, the cache upsert, and the graceful-fallback paths all run while CI
(which has no ANTHROPIC_API_KEY) stays offline and free.

The locked principle — "the app computes the numbers, the model only narrates" —
is exercised by compute_month_facts() running directly against seeded data.
"""
from datetime import date

import app.ai as ai
from app.ai import _Insight, ParseError
from app.blueprints.insights import compute_month_facts
from tests.conftest import (
    create_account,
    create_budget,
    create_category,
    create_insight,
    create_transaction,
    fetch_insight,
)

HX = {"HX-Request": "true"}


class _Seam:
    """Stand-in for _call_insight_model that counts calls so cache-hit and
    no-API-call paths can be asserted."""
    def __init__(self, result=None, boom=False):
        self.calls = 0
        self.result = result or _Insight(summary="Looking solid this month.",
                                         tips=["Keep it up", "Watch groceries"])
        self.boom = boom

    def __call__(self, *a, **k):
        self.calls += 1
        if self.boom:
            raise ParseError("network down")
        return self.result


def _this_month():
    t = date.today()
    return t.year, t.month


def _prev_month():
    """The last complete month — what the dashboard Insight card now recaps."""
    t = date.today()
    return (t.year - 1, 12) if t.month == 1 else (t.year, t.month - 1)


# --- compute_month_facts (deterministic, DB-backed) -------------------------

def test_compute_month_facts_figures_and_overruns(users):
    a = users["a"]["id"]
    acct = create_account(a, "facts-acct")
    groceries = create_category(a, "facts-groceries")
    rent = create_category(a, "facts-rent")
    # A fixed month with no other activity.
    create_transaction(a, acct, 1000, "2026-03-05", "income")
    create_transaction(a, acct, 300, "2026-03-10", "expense", category_id=groceries)
    create_transaction(a, acct, 100, "2026-03-12", "expense", category_id=rent)
    create_budget(a, groceries, 200)  # actual 300 → overrun of 100

    facts = compute_month_facts(a, 2026, 3)
    assert facts["income"] == 1000
    assert facts["expenses"] == 400
    assert facts["net"] == 600
    assert facts["savings_rate"] == 60.0
    # top category first, with amounts
    assert facts["top_categories"][0] == {"name": "facts-groceries", "amount": 300.0}
    assert {"category": "facts-groceries", "budget": 200.0,
            "actual": 300.0, "over": 100.0} in facts["overruns"]


def test_compute_month_facts_only_sees_own_rows(users):
    a, b = users["a"]["id"], users["b"]["id"]
    acct_a = create_account(a, "iso-a")
    acct_b = create_account(b, "iso-b")
    create_transaction(a, acct_a, 50, "2026-04-02", "income")
    create_transaction(b, acct_b, 9999, "2026-04-02", "income")  # B's money
    facts = compute_month_facts(a, 2026, 4)
    assert facts["income"] == 50          # B's 9999 never leaks in
    assert facts["savings_rate"] == 100.0  # no A expenses that month


def test_compute_month_facts_empty_month_is_zeroed(users):
    a = users["a"]["id"]
    facts = compute_month_facts(a, 2000, 1)
    assert facts["income"] == 0 and facts["expenses"] == 0
    assert facts["savings_rate"] is None
    assert facts["top_categories"] == [] and facts["overruns"] == []


# --- route: auth + fragment shape ------------------------------------------

def test_generate_requires_login(anon_client):
    resp = anon_client.post("/insights/generate", data={})
    assert resp.status_code == 302


def test_generate_returns_card_fragment_and_caches(client_a, users, monkeypatch):
    # User A has the fixture's current-month expense, so the month has data.
    year, month = _this_month()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seam = _Seam(_Insight(summary="June is on track.", tips=["Nice work"]))
    monkeypatch.setattr(ai, "_call_insight_model", seam)

    resp = client_a.post("/insights/generate",
                         data={"year": year, "month": month}, headers=HX)
    assert resp.status_code == 200
    assert b"<html" not in resp.data            # a fragment, not a full page
    assert b"June is on track." in resp.data    # the model's narration shows
    assert "showToast" in resp.headers.get("HX-Trigger", "")
    assert seam.calls == 1
    # cached for the user
    row = fetch_insight(users["a"]["id"], year, month)
    assert row is not None
    assert "June is on track." in row[0]


# --- cache hit: dashboard load must NOT call the model ----------------------

def test_dashboard_uses_cache_without_calling_model(client_a, users, monkeypatch):
    # The dashboard Insight card recaps the previous complete month, so seed
    # prior-month activity (making the card show), generate that month's digest,
    # then confirm the dashboard reuses the cache without another model call.
    a = users["a"]
    year, month = _prev_month()
    create_transaction(a["id"], a["account_id"], 30, date(year, month, 15),
                       transaction_type="expense", category_id=a["category_id"])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seam = _Seam(_Insight(summary="Cached recap text.", tips=["Tip one"]))
    monkeypatch.setattr(ai, "_call_insight_model", seam)

    # Generate once (seam called), then load the dashboard (must reuse the cache).
    client_a.post("/insights/generate",
                  data={"year": year, "month": month}, headers=HX)
    assert seam.calls == 1
    resp = client_a.get("/")
    assert resp.status_code == 200
    assert b"Cached recap text." in resp.data    # cached narrative rendered
    assert seam.calls == 1                        # dashboard never hit the model


# --- graceful fallback ------------------------------------------------------

def test_generate_api_error_falls_back(client_a, users, monkeypatch):
    year, month = _this_month()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_call_insight_model", _Seam(boom=True))
    resp = client_a.post("/insights/generate",
                         data={"year": year, "month": month}, headers=HX)
    assert resp.status_code == 200
    assert b"Generate insight" in resp.data       # back to the un-generated card
    assert "showToast" in resp.headers.get("HX-Trigger", "")
    assert fetch_insight(users["a"]["id"], year, month) is None  # nothing cached


def test_generate_no_data_month_skips_model(client_a, users, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seam = _Seam()
    monkeypatch.setattr(ai, "_call_insight_model", seam)
    resp = client_a.post("/insights/generate",
                         data={"year": 2000, "month": 1}, headers=HX)
    assert resp.status_code == 200
    assert "showToast" in resp.headers.get("HX-Trigger", "")
    assert seam.calls == 0                         # no figures → no API call
    assert fetch_insight(users["a"]["id"], 2000, 1) is None


# --- isolation: A cannot see B's cached insight -----------------------------

def test_dashboard_never_shows_another_users_insight(client_a, users, monkeypatch):
    # Seed A's prior-month activity so A's card renders, then plant B's cached
    # digest for that same (read) month — A must never see it.
    a = users["a"]
    year, month = _prev_month()
    create_transaction(a["id"], a["account_id"], 30, date(year, month, 15),
                       transaction_type="expense", category_id=a["category_id"])
    create_insight(users["b"]["id"], year, month,
                   {"summary": "B-PRIVATE-SUMMARY", "tips": []})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    resp = client_a.get("/")
    assert resp.status_code == 200
    assert b"B-PRIVATE-SUMMARY" not in resp.data
