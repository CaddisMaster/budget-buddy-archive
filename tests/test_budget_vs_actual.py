"""Unit tests for compute_budget_vs_actual() (budgets blueprint).

As of v7.0 a budget is a single monthly amount per category (no period dates).
`actual` is the sum of that user's non-adjustment, non-transfer expense
transactions in the same category during the queried calendar month; with no
month given the helper defaults to the current month. Seed a budget + dated
transactions and assert the (category, budget, actual, remaining) it returns.

These run against the dev Postgres via the `users` fixture (USER_A); all seeded
rows are cleaned up when that fixture tears the test users down.
"""
from datetime import datetime

import pytest

from app.blueprints.budgets import compute_budget_vs_actual
from tests.conftest import create_budget, create_category, create_transaction


def _row_for(rows, name):
    return next((r for r in rows if r[0] == name), None)


@pytest.fixture
def bva(users):
    """A budget of 100 with a mix of in-month, other-month, adjustment, and
    income transactions in June 2026 — only the two in-month expenses (50 total)
    should count toward `actual`."""
    a = users["a"]
    cid = create_category(a["id"], "BVA")
    create_budget(a["id"], cid, 100.0)
    acct = a["account_id"]
    create_transaction(a["id"], acct, 30.0, "2026-06-10", category_id=cid)
    create_transaction(a["id"], acct, 20.0, "2026-06-15", category_id=cid)
    create_transaction(a["id"], acct, 999.0, "2026-07-05", category_id=cid)            # other month
    create_transaction(a["id"], acct, 5.0, "2026-06-12", category_id=cid,
                       is_adjustment=True)                                              # excluded
    create_transaction(a["id"], acct, 200.0, "2026-06-12", category_id=cid,
                       transaction_type="income")                                       # excluded
    return {"a": a, "cid": cid}


def test_budget_amount_returned(bva):
    row = _row_for(compute_budget_vs_actual(bva["a"]["id"], "2026", "06"), "BVA")
    assert row is not None
    assert float(row[1]) == 100.0


def test_actual_only_counts_in_month_non_adjustment_expenses(bva):
    row = _row_for(compute_budget_vs_actual(bva["a"]["id"], "2026", "06"), "BVA")
    # 30 + 20 only; 999 (July), 5 (adjustment), 200 (income) all excluded.
    assert float(row[2]) == 50.0


def test_remaining_is_budget_minus_actual(bva):
    row = _row_for(compute_budget_vs_actual(bva["a"]["id"], "2026", "06"), "BVA")
    assert float(row[3]) == 50.0


def test_over_budget_remaining_goes_negative(users):
    a = users["a"]
    cid = create_category(a["id"], "Tight")
    create_budget(a["id"], cid, 40.0)
    create_transaction(a["id"], a["account_id"], 50.0, "2026-06-10", category_id=cid)
    row = _row_for(compute_budget_vs_actual(a["id"], "2026", "06"), "Tight")
    assert float(row[2]) == 50.0
    assert float(row[3]) == -10.0


def test_actual_scoped_to_queried_month(bva):
    # The July expense (999) only shows up when July is the queried month.
    july = _row_for(compute_budget_vs_actual(bva["a"]["id"], "2026", "07"), "BVA")
    assert float(july[2]) == 999.0
    june = _row_for(compute_budget_vs_actual(bva["a"]["id"], "2026", "06"), "BVA")
    assert float(june[2]) == 50.0


def test_no_month_defaults_to_current_month(users):
    # With no year/month, actual is this calendar month's spend.
    a = users["a"]
    cid = create_category(a["id"], "Now")
    create_budget(a["id"], cid, 500.0)
    today = datetime.today().strftime("%Y-%m-%d")
    create_transaction(a["id"], a["account_id"], 70.0, today, category_id=cid)
    row = _row_for(compute_budget_vs_actual(a["id"]), "Now")
    assert row is not None
    assert float(row[2]) == 70.0


def test_only_returns_own_budgets(bva, users):
    # USER_B has no budgets, so A's "BVA" must never appear in B's result.
    b_rows = compute_budget_vs_actual(users["b"]["id"], "2026", "06")
    assert _row_for(b_rows, "BVA") is None
