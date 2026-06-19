"""Unit tests for compute_budget_suggestions() (budgets blueprint).

The suggested monthly budget for a category is its average monthly spend over
the last 6 months — non-adjustment, non-transfer expense transactions only. The
cockpit uses it as the default for any category the user hasn't set. Returns a
dict {category_id: amount}; a category only appears with >= 1 month of history.

Uses dates relative to today so the 6-month window always catches them.
"""
from datetime import datetime, timedelta

from app.blueprints.budgets import compute_budget_suggestions
from tests.conftest import create_category, create_transaction


def _this_month_day(day=10):
    return datetime.today().replace(day=day).strftime("%Y-%m-%d")


def _prev_month_day(day=10):
    # ~35 days back guarantees a different (earlier) calendar month, still inside
    # the 6-month window.
    d = (datetime.today().replace(day=1) - timedelta(days=20)).replace(day=day)
    return d.strftime("%Y-%m-%d")


def test_suggestion_is_average_of_monthly_totals(users):
    a = users["a"]
    cid = create_category(a["id"], "SuggAvg")
    acct = a["account_id"]
    # 200 this month, 100 last month -> average 150.
    create_transaction(a["id"], acct, 120.0, _this_month_day(), category_id=cid)
    create_transaction(a["id"], acct, 80.0, _this_month_day(12), category_id=cid)
    create_transaction(a["id"], acct, 100.0, _prev_month_day(), category_id=cid)
    suggestions = compute_budget_suggestions(a["id"])
    assert cid in suggestions
    assert suggestions[cid] == 150.0


def test_excludes_adjustments_income_and_old_months(users):
    a = users["a"]
    cid = create_category(a["id"], "SuggExcl")
    acct = a["account_id"]
    create_transaction(a["id"], acct, 90.0, _this_month_day(), category_id=cid)
    create_transaction(a["id"], acct, 500.0, _this_month_day(11), category_id=cid,
                       is_adjustment=True)                                   # excluded
    create_transaction(a["id"], acct, 500.0, _this_month_day(12), category_id=cid,
                       transaction_type="income")                           # excluded
    # >6 months ago -> outside the window
    old = (datetime.today() - timedelta(days=300)).strftime("%Y-%m-%d")
    create_transaction(a["id"], acct, 999.0, old, category_id=cid)
    suggestions = compute_budget_suggestions(a["id"])
    assert suggestions[cid] == 90.0


def test_category_with_no_history_absent(users):
    a = users["a"]
    cid = create_category(a["id"], "SuggEmpty")   # no transactions
    suggestions = compute_budget_suggestions(a["id"])
    assert cid not in suggestions


def test_suggestions_are_per_user(users):
    a, b = users["a"], users["b"]
    cid = create_category(a["id"], "SuggMine")
    create_transaction(a["id"], a["account_id"], 60.0, _this_month_day(), category_id=cid)
    assert cid not in compute_budget_suggestions(b["id"])
    assert cid in compute_budget_suggestions(a["id"])
