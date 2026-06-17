"""Unit tests for compute_budget_vs_actual() (budgets blueprint).

The budget-vs-actual numbers shown on /analytics come from one SQL query that
joins budgets to in-period expense transactions. v5.0 lifted it into a helper so
it can be exercised directly: seed a budget + transactions with known dates and
assert the (category, budget, actual, remaining) it returns.

These run against the dev Postgres via the `users` fixture (USER_A); all seeded
rows are cleaned up when that fixture tears the test users down.
"""
import pytest

from app.blueprints.budgets import compute_budget_vs_actual
from tests.conftest import create_budget, create_category, create_transaction


def _row_for(rows, name):
    return next((r for r in rows if r[0] == name), None)


@pytest.fixture
def bva(users):
    """A June budget of 100 with a mix of in-period, out-of-period, adjustment,
    and income transactions — only the two in-period expenses (50 total) should
    count toward `actual`."""
    a = users["a"]
    cid = create_category(a["id"], "BVA")
    create_budget(a["id"], cid, 100.0, "2026-06-01", "2026-06-30")
    acct = a["account_id"]
    create_transaction(a["id"], acct, 30.0, "2026-06-10", category_id=cid)
    create_transaction(a["id"], acct, 20.0, "2026-06-15", category_id=cid)
    create_transaction(a["id"], acct, 999.0, "2026-07-05", category_id=cid)            # out of period
    create_transaction(a["id"], acct, 5.0, "2026-06-12", category_id=cid,
                       is_adjustment=True)                                              # excluded
    create_transaction(a["id"], acct, 200.0, "2026-06-12", category_id=cid,
                       transaction_type="income")                                       # excluded
    return {"a": a, "cid": cid}


def test_budget_amount_returned(bva):
    row = _row_for(compute_budget_vs_actual(bva["a"]["id"], "2026", "06"), "BVA")
    assert row is not None
    assert float(row[1]) == 100.0


def test_actual_only_counts_in_period_non_adjustment_expenses(bva):
    row = _row_for(compute_budget_vs_actual(bva["a"]["id"], "2026", "06"), "BVA")
    # 30 + 20 only; 999 (July), 5 (adjustment), 200 (income) all excluded.
    assert float(row[2]) == 50.0


def test_remaining_is_budget_minus_actual(bva):
    row = _row_for(compute_budget_vs_actual(bva["a"]["id"], "2026", "06"), "BVA")
    assert float(row[3]) == 50.0


def test_over_budget_remaining_goes_negative(users):
    a = users["a"]
    cid = create_category(a["id"], "Tight")
    create_budget(a["id"], cid, 40.0, "2026-06-01", "2026-06-30")
    create_transaction(a["id"], a["account_id"], 50.0, "2026-06-10", category_id=cid)
    row = _row_for(compute_budget_vs_actual(a["id"], "2026", "06"), "Tight")
    assert float(row[2]) == 50.0
    assert float(row[3]) == -10.0


def test_month_filter_excludes_other_month_budgets(bva, users):
    a = bva["a"]
    may_cat = create_category(a["id"], "MayOnly")
    create_budget(a["id"], may_cat, 75.0, "2026-05-01", "2026-05-31")

    june_rows = compute_budget_vs_actual(a["id"], "2026", "06")
    assert _row_for(june_rows, "BVA") is not None
    assert _row_for(june_rows, "MayOnly") is None   # filtered out

    all_rows = compute_budget_vs_actual(a["id"])     # no month filter
    assert _row_for(all_rows, "BVA") is not None
    assert _row_for(all_rows, "MayOnly") is not None


def test_only_returns_own_budgets(bva, users):
    # USER_B has no budgets, so A's "BVA" must never appear in B's result.
    b_rows = compute_budget_vs_actual(users["b"]["id"], "2026", "06")
    assert _row_for(b_rows, "BVA") is None
