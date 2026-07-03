"""v10.8 tests — "Goal Coach" (/goals/coach/generate + app.ai).

The twin of test_insight.py, pointed at the Goals page. No real Anthropic API
calls: the single network seam, app.ai._call_coach_model, is monkeypatched to
return a canned _Coach, so the route, the deterministic fact-builder, the cache
upsert, and the graceful-fallback paths all run while CI (which has no
ANTHROPIC_API_KEY) stays offline and free.

The locked principle — "the app computes the numbers, the model only narrates" —
is exercised by compute_goal_coach_facts() running directly against seeded goals.
"""
from datetime import date

import app.ai as ai
from app.ai import _Coach, ParseError
from app.blueprints.goals import compute_goal_coach_facts
from app.db import db_cursor
from tests.conftest import (
    create_account,
    create_goal,
    create_goal_coach,
    create_transaction,
    fetch_goal_coach,
)

HX = {"HX-Request": "true"}


class _Seam:
    """Stand-in for _call_coach_model that counts calls so cache-hit and
    no-API-call paths can be asserted."""
    def __init__(self, result=None, boom=False):
        self.calls = 0
        self.result = result or _Coach(summary="You're tracking well.",
                                       tips=["Automate a transfer"])
        self.boom = boom

    def __call__(self, *a, **k):
        self.calls += 1
        if self.boom:
            raise ParseError("network down")
        return self.result


def _this_month():
    t = date.today()
    return t.year, t.month


def _seed_incomplete_goal(user):
    """Give a user one account funded below a fresh goal's target, so exactly one
    in-progress goal exists (saved 300 of 1000)."""
    acct = create_account(user["id"], "coach-acct")
    create_transaction(user["id"], acct, 300, date.today(), "income")
    create_goal(user["id"], acct, 1000, baseline=0)
    return acct


# --- compute_goal_coach_facts (deterministic, DB-backed) --------------------

def test_compute_goal_coach_facts_figures(users):
    a = users["a"]["id"]
    acct = create_account(a, "gc-acct")
    create_transaction(a, acct, 400, date.today(), "income")
    create_goal(a, acct, 1000, baseline=0)  # saved 400 of 1000 → 40%
    with db_cursor() as cursor:
        facts = compute_goal_coach_facts(cursor, a)
    assert facts["count"] == 1
    assert facts["incomplete_count"] == 1
    assert facts["total_saved"] == 400.0
    assert facts["total_target"] == 1000.0
    assert facts["goals"][0]["name"] == "seed-goal"
    assert facts["goals"][0]["type"] == "save"
    assert facts["goals"][0]["percent"] == 40.0
    assert facts["goals"][0]["remaining"] == 600.0


def test_compute_goal_coach_facts_carries_payoff_type(users):
    # The coach narrates payoff goals as debt-paydown — the type must reach the
    # facts payload. Paid 200 of a 500 starting debt → 40%, 300 still owed.
    a = users["a"]["id"]
    acct = create_account(a, "gc-card")
    create_transaction(a, acct, 500, date.today(), "expense")   # the debt
    create_transaction(a, acct, 200, date.today(), "income")    # a payment
    create_goal(a, acct, 500, baseline=-500, goal_type="payoff")
    with db_cursor() as cursor:
        facts = compute_goal_coach_facts(cursor, a)
    assert facts["goals"][0]["type"] == "payoff"
    assert facts["goals"][0]["saved"] == 200.0
    assert facts["goals"][0]["remaining"] == 300.0
    assert facts["incomplete_count"] == 1


def test_compute_goal_coach_facts_only_sees_own_rows(users):
    a, b = users["a"]["id"], users["b"]["id"]
    acct_a = create_account(a, "gc-a")
    acct_b = create_account(b, "gc-b")
    create_transaction(a, acct_a, 100, date.today(), "income")
    create_transaction(b, acct_b, 9999, date.today(), "income")  # B's money
    create_goal(a, acct_a, 500)
    create_goal(b, acct_b, 500)
    with db_cursor() as cursor:
        facts = compute_goal_coach_facts(cursor, a)
    assert facts["count"] == 1
    assert facts["total_saved"] == 100.0   # B's 9999 never leaks in


def test_compute_goal_coach_facts_empty_when_no_goals(users):
    a = users["a"]["id"]  # A has seed data but no goals
    with db_cursor() as cursor:
        facts = compute_goal_coach_facts(cursor, a)
    assert facts["count"] == 0
    assert facts["incomplete_count"] == 0
    assert facts["total_saved"] == 0
    assert facts["goals"] == []


# --- route: auth + fragment shape ------------------------------------------

def test_generate_requires_login(anon_client):
    resp = anon_client.post("/goals/coach/generate", data={})
    assert resp.status_code == 302


def test_generate_returns_card_fragment_and_caches(client_a, users, monkeypatch):
    _seed_incomplete_goal(users["a"])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seam = _Seam(_Coach(summary="Nice pace on your goals.", tips=["Automate it"]))
    monkeypatch.setattr(ai, "_call_coach_model", seam)

    resp = client_a.post("/goals/coach/generate", headers=HX)
    assert resp.status_code == 200
    assert b"<html" not in resp.data                 # a fragment, not a full page
    assert b"Nice pace on your goals." in resp.data  # the model's narration shows
    assert "showToast" in resp.headers.get("HX-Trigger", "")
    assert seam.calls == 1
    year, month = _this_month()
    row = fetch_goal_coach(users["a"]["id"], year, month)
    assert row is not None
    assert "Nice pace on your goals." in row[0]


# --- cache hit: /goals load must NOT call the model -------------------------

def test_goals_page_uses_cache_without_calling_model(client_a, users, monkeypatch):
    _seed_incomplete_goal(users["a"])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seam = _Seam(_Coach(summary="Cached coaching text.", tips=["Tip one"]))
    monkeypatch.setattr(ai, "_call_coach_model", seam)

    client_a.post("/goals/coach/generate", headers=HX)   # generate (seam called)
    assert seam.calls == 1
    resp = client_a.get("/goals")                        # load reuses the cache
    assert resp.status_code == 200
    assert b"Cached coaching text." in resp.data
    assert seam.calls == 1                               # page never hit the model


# --- graceful fallback ------------------------------------------------------

def test_generate_api_error_falls_back(client_a, users, monkeypatch):
    _seed_incomplete_goal(users["a"])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_call_coach_model", _Seam(boom=True))
    resp = client_a.post("/goals/coach/generate", headers=HX)
    assert resp.status_code == 200
    assert b"Generate coaching" in resp.data          # back to the un-generated card
    assert "showToast" in resp.headers.get("HX-Trigger", "")
    year, month = _this_month()
    assert fetch_goal_coach(users["a"]["id"], year, month) is None  # nothing cached


def test_generate_no_goals_skips_model(client_a, users, monkeypatch):
    # A has no in-progress goals, so the model must never be called.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seam = _Seam()
    monkeypatch.setattr(ai, "_call_coach_model", seam)
    resp = client_a.post("/goals/coach/generate", headers=HX)
    assert resp.status_code == 200
    assert "showToast" in resp.headers.get("HX-Trigger", "")
    assert seam.calls == 0
    year, month = _this_month()
    assert fetch_goal_coach(users["a"]["id"], year, month) is None


# --- isolation: A cannot see B's cached coaching ----------------------------

def test_goals_page_never_shows_another_users_coach(client_a, users, monkeypatch):
    _seed_incomplete_goal(users["a"])   # A's card must render for a fair test
    year, month = _this_month()
    create_goal_coach(users["b"]["id"], year, month,
                      {"summary": "B-PRIVATE-COACH", "tips": []})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    resp = client_a.get("/goals")
    assert resp.status_code == 200
    assert b"B-PRIVATE-COACH" not in resp.data


# --- glow-up smoke: summary strip + themed cards ----------------------------

def test_goals_page_renders_summary_strip(client_a, users):
    _seed_incomplete_goal(users["a"])
    resp = client_a.get("/goals")
    assert resp.status_code == 200
    assert b"goal-summary" in resp.data      # overall progress strip
    assert b"goal-card" in resp.data         # reskinned card
    assert b"% overall" in resp.data


def test_dashboard_renders_goal_grid(client_a, users):
    _seed_incomplete_goal(users["a"])
    resp = client_a.get("/dashboard")
    assert resp.status_code == 200
    assert b"goal-grid" in resp.data
    assert b"goal-summary" in resp.data
