"""v10.2 tests — month-ahead "Forecast" projection (/forecasts/generate + app.ai).

No real Anthropic API calls: the single network seam, app.ai._call_forecast_model,
is monkeypatched to return a canned _Forecast, so the route, the deterministic
projection builder, the cache upsert, and the graceful-fallback paths all run
while CI (no ANTHROPIC_API_KEY) stays offline and free.

The locked principle — "the app computes the numbers, the model only narrates" —
is exercised by the pure project_expenses() day-weighting math and by
compute_forecast() running directly against seeded data.
"""
import calendar
from datetime import date

import pytest
from dateutil.relativedelta import relativedelta

import app.ai as ai
from app.ai import _Forecast, ParseError
from app.blueprints.forecasts import compute_forecast, project_expenses
from tests.conftest import (
    create_account,
    create_budget,
    create_category,
    create_schedule,
    create_transaction,
    create_forecast,
    fetch_forecast,
)

HX = {"HX-Request": "true"}


class _Seam:
    """Stand-in for _call_forecast_model that counts calls so cache-hit and
    no-API-call paths can be asserted."""
    def __init__(self, result=None, boom=False):
        self.calls = 0
        self.result = result or _Forecast(summary="On pace to finish in the green.",
                                          tips=["Hold the line on dining out"])
        self.boom = boom

    def __call__(self, *a, **k):
        self.calls += 1
        if self.boom:
            raise ParseError("network down")
        return self.result


def _this_month():
    t = date.today()
    return t.year, t.month


# --- project_expenses (pure, no DB) -----------------------------------------

def test_project_expenses_flat_when_no_history():
    # day 10 of 30, spent 100, no history → flat run-rate 100/10*30 = 300.
    amount, method = project_expenses(100, 10, 30, {})
    assert amount == 300.0
    assert method == "flat"


def test_project_expenses_day_weighted_corrects_for_shape():
    # By day 12, history says ~40% of a month's spend has landed (400 of 1000),
    # so $340 so far projects to 340 / 0.4 = $850.
    history = {5: 400.0, 20: 600.0}
    amount, method = project_expenses(340, 12, 30, history)
    assert amount == 850.0
    assert method == "day_weighted"


def test_project_expenses_floor_falls_back_to_flat():
    # Day 1 with all historical spend landing late → cum fraction ~0 (< floor) →
    # flat, not a divide-by-zero blowup.
    history = {25: 1000.0}
    amount, method = project_expenses(50, 1, 30, history)
    assert method == "flat"
    assert amount == 50 / 1 * 30


def test_project_expenses_tiny_history_falls_back_to_flat():
    # Total history below MIN_HISTORY → don't trust the shape.
    amount, method = project_expenses(100, 10, 30, {5: 40.0})
    assert method == "flat"
    assert amount == 300.0


def test_project_expenses_complete_month_returns_actual():
    assert project_expenses(500, 30, 30, {5: 999.0}) == (500.0, "flat")


def test_project_expenses_before_month_starts_is_zero():
    assert project_expenses(0, 0, 31, {}) == (0.0, "flat")


# --- compute_forecast (deterministic, DB-backed) ----------------------------

def test_compute_forecast_only_sees_own_rows(users):
    a, b = users["a"]["id"], users["b"]["id"]
    acct_a = create_account(a, "fc-iso-a")
    acct_b = create_account(b, "fc-iso-b")
    today = date.today().isoformat()
    create_transaction(a, acct_a, 50, today, "income")
    create_transaction(b, acct_b, 9999, today, "income")  # B's money
    fc = compute_forecast(a, *_this_month())
    assert fc["income_to_date"] == 50.0    # B's 9999 never leaks in
    assert fc["projected_income"] >= 50.0


def test_compute_forecast_empty_month_is_zeroed(users):
    a = users["a"]["id"]
    fc = compute_forecast(a, 2000, 1)
    assert fc["income_to_date"] == 0 and fc["expenses_to_date"] == 0
    assert fc["projected_expenses"] == 0 and fc["projected_income"] == 0
    assert fc["remaining_items"] == []
    assert fc["method"] == "flat"


def test_compute_forecast_includes_remaining_scheduled_income(users):
    a = users["a"]["id"]
    today = date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    if today.day >= days_in_month:
        pytest.skip("last day of month — no remaining-this-month window")
    acct = create_account(a, "fc-sched")
    due = date(today.year, today.month, days_in_month)  # later this month
    create_schedule(a, acct, 2000, "monthly", due, transaction_type="income")
    fc = compute_forecast(a, today.year, today.month)
    assert fc["remaining_scheduled_income"] == 2000.0
    assert any(i["due"] == due.isoformat() for i in fc["remaining_items"])
    assert fc["projected_income"] >= 2000.0


def test_compute_forecast_excludes_other_users_schedules(users):
    a, b = users["a"]["id"], users["b"]["id"]
    today = date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    if today.day >= days_in_month:
        pytest.skip("last day of month — no remaining-this-month window")
    acct_b = create_account(b, "fc-sched-b")
    due = date(today.year, today.month, days_in_month)
    create_schedule(b, acct_b, 5000, "monthly", due, transaction_type="income")
    fc = compute_forecast(a, today.year, today.month)
    assert fc["remaining_scheduled_income"] == 0.0   # B's schedule never leaks


def test_compute_forecast_includes_remaining_scheduled_expense(users):
    a = users["a"]["id"]
    today = date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    if today.day >= days_in_month:
        pytest.skip("last day of month — no remaining-this-month window")
    acct = create_account(a, "fc-sched-exp")
    due = date(today.year, today.month, days_in_month)
    create_schedule(a, acct, 1500, "monthly", due, transaction_type="expense")
    fc = compute_forecast(a, today.year, today.month)
    assert fc["remaining_scheduled_expense"] == 1500.0
    assert any(i["due"] == due.isoformat() and i["type"] == "expense"
               for i in fc["remaining_items"])
    # Remaining scheduled expense is surfaced as CONTEXT only — it is NOT added on
    # top of the day-weighted projection (the curve already encodes recurring-bill
    # timing). The projection reflects only month-to-date spend (the small fixture
    # seed), so the $1500 bill never inflates it.
    assert fc["projected_expenses"] < 1500.0


def test_compute_forecast_day_weighted_path(users):
    a = users["a"]["id"]
    today = date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    if today.day >= days_in_month:
        pytest.skip("last day of month — complete month returns the actual (flat)")
    acct = create_account(a, "fc-dw")
    month_first = date(today.year, today.month, 1)
    # 6 months of history, all dated the 1st → cumulative fraction by any day ~1.0,
    # total $600 (well over the MIN_HISTORY floor) → the day-weighted path engages.
    for i in range(1, 7):
        d = month_first - relativedelta(months=i)
        create_transaction(a, acct, 100, d.isoformat(), "expense")
    create_transaction(a, acct, 80, today.isoformat(), "expense")  # some MTD spend
    fc = compute_forecast(a, today.year, today.month)
    assert fc["method"] == "day_weighted"


def test_compute_forecast_over_budget_excludes_unbudgeted(users):
    a = users["a"]["id"]
    today = date.today()
    acct = create_account(a, "fc-bud")
    budgeted = create_category(a, "fc-budgeted")
    unbudgeted = create_category(a, "fc-unbudgeted")
    create_budget(a, budgeted, 5000)
    create_transaction(a, acct, 100, today.isoformat(), "expense", category_id=budgeted)
    create_transaction(a, acct, 50000, today.isoformat(), "expense", category_id=unbudgeted)
    fc = compute_forecast(a, today.year, today.month)
    assert fc["projected_over_budget"] is not None
    # The unbudgeted $50k must NOT count against the $5k budget (the v10.2.1 fix).
    # projected budgeted spend is at most 100×days_in_month < 5000 → always under.
    assert fc["projected_over_budget"] < 0
    factor = fc["projected_expenses"] / fc["expenses_to_date"]
    assert fc["projected_over_budget"] == pytest.approx(100 * factor - 5000, abs=1.0)


def test_compute_forecast_past_month_uses_actual(users):
    a = users["a"]["id"]
    acct = create_account(a, "fc-past")
    create_transaction(a, acct, 200, "2020-03-10", "expense")
    create_transaction(a, acct, 500, "2020-03-05", "income")
    fc = compute_forecast(a, 2020, 3)
    assert fc["day_of_month"] == 31            # fully elapsed
    assert fc["expenses_to_date"] == 200
    assert fc["projected_expenses"] == 200     # complete month → the actual
    assert fc["projected_income"] == 500
    assert fc["method"] == "flat"


def test_compute_forecast_future_month_is_zeroed(users):
    a = users["a"]["id"]
    fc = compute_forecast(a, date.today().year + 1, 1)
    assert fc["day_of_month"] == 0             # month hasn't started
    assert fc["projected_expenses"] == 0 and fc["projected_income"] == 0
    assert fc["remaining_items"] == []


def test_forecast_materialized_schedule_not_double_counted(users):
    a = users["a"]["id"]
    today = date.today()
    acct = create_account(a, "fc-dc")
    create_schedule(a, acct, 2222, "monthly", today, transaction_type="income")
    from app.blueprints.schedules import run_due_schedules
    run_due_schedules(a)   # materializes today's occurrence + advances next_due
    fc = compute_forecast(a, today.year, today.month)
    assert fc["income_to_date"] >= 2222        # now a real transaction
    # ...and it must NOT also be projected as a remaining item (no double-count).
    assert fc["remaining_scheduled_income"] == 0.0
    assert all(i["type"] != "income" or i["amount"] != 2222.0
               for i in fc["remaining_items"])


# --- route: auth + fragment shape ------------------------------------------

def test_generate_requires_login(anon_client):
    resp = anon_client.post("/forecasts/generate", data={})
    assert resp.status_code == 302


def test_generate_returns_card_fragment_and_caches(client_a, users, monkeypatch):
    # User A has the fixture's current-month expense, so the month has data.
    year, month = _this_month()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seam = _Seam(_Forecast(summary="June trends positive.", tips=["Nice work"]))
    monkeypatch.setattr(ai, "_call_forecast_model", seam)

    resp = client_a.post("/forecasts/generate",
                         data={"year": year, "month": month}, headers=HX)
    assert resp.status_code == 200
    assert b"<html" not in resp.data             # a fragment, not a full page
    assert b"June trends positive." in resp.data  # the model's narration shows
    assert "showToast" in resp.headers.get("HX-Trigger", "")
    assert seam.calls == 1
    row = fetch_forecast(users["a"]["id"], year, month)
    assert row is not None
    assert "June trends positive." in row[0]


# --- cache hit: dashboard load must NOT call the model ----------------------

def test_dashboard_uses_cache_without_calling_model(client_a, users, monkeypatch):
    year, month = _this_month()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seam = _Seam(_Forecast(summary="Cached outlook text.", tips=["Tip one"]))
    monkeypatch.setattr(ai, "_call_forecast_model", seam)

    client_a.post("/forecasts/generate",
                  data={"year": year, "month": month}, headers=HX)
    assert seam.calls == 1
    resp = client_a.get("/dashboard")
    assert resp.status_code == 200
    assert b"Cached outlook text." in resp.data   # cached narrative rendered
    assert seam.calls == 1                         # dashboard never hit the model


# --- graceful fallback ------------------------------------------------------

def test_generate_api_error_falls_back(client_a, users, monkeypatch):
    year, month = _this_month()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_call_forecast_model", _Seam(boom=True))
    resp = client_a.post("/forecasts/generate",
                         data={"year": year, "month": month}, headers=HX)
    assert resp.status_code == 200
    assert b"Generate forecast" in resp.data       # back to the un-generated card
    assert "showToast" in resp.headers.get("HX-Trigger", "")
    assert fetch_forecast(users["a"]["id"], year, month) is None  # nothing cached


def test_generate_no_data_month_skips_model(client_a, users, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seam = _Seam()
    monkeypatch.setattr(ai, "_call_forecast_model", seam)
    resp = client_a.post("/forecasts/generate",
                         data={"year": 2000, "month": 1}, headers=HX)
    assert resp.status_code == 200
    assert "showToast" in resp.headers.get("HX-Trigger", "")
    assert seam.calls == 0                          # no figures → no API call
    assert fetch_forecast(users["a"]["id"], 2000, 1) is None


# --- isolation: A cannot see B's cached forecast ----------------------------

def test_dashboard_never_shows_another_users_forecast(client_a, users, monkeypatch):
    year, month = _this_month()
    create_forecast(users["b"]["id"], year, month,
                    {"summary": "B-PRIVATE-FORECAST", "tips": []})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    resp = client_a.get("/dashboard")
    assert resp.status_code == 200
    assert b"B-PRIVATE-FORECAST" not in resp.data
