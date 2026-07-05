"""v10.9 tests — Budget report (the last-6-complete-months hit/miss grid on /budgets).

The pure derivation (build_budget_report) is tested with explicit month lists —
no clock dependence. DB-backed load_budget_report tests derive their seed dates
from _report_months() (the 15th of a labelled month), NOT timedelta arithmetic,
so they can't flake near a month boundary. The conftest `users` fixture seeds
only a today-dated transaction, which is OUTSIDE the report window — so a bare
client doubles as the hidden-section case.
"""
from datetime import date

from app.blueprints.budgets import (
    _report_months, build_budget_report, load_budget_report,
)
from app.helpers import recent_months
from conftest import (
    create_budget, create_category, create_transaction, create_transfer,
)

MONTHS = ['2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06']


def _row(report, name):
    return next((r for r in report["rows"] if r["name"] == name), None)


def _spend(cid, name, month_amounts):
    """Sparse spend rows for one category: {month: amount}."""
    return [(cid, name, m, amt) for m, amt in month_amounts.items()]


# --- _report_months (pure window helper) ------------------------------------

def test_report_months_excludes_current_oldest_first():
    assert _report_months(today=date(2026, 7, 5)) == MONTHS


def test_report_months_across_year_boundary():
    assert _report_months(today=date(2026, 2, 10)) == [
        '2025-08', '2025-09', '2025-10', '2025-11', '2025-12', '2026-01']


# --- build_budget_report (pure) ----------------------------------------------

def test_cells_align_to_months_and_zero_fill():
    report = build_budget_report(
        MONTHS, _spend(1, 'Groceries', {'2026-02': 120.0, '2026-05': 80.0}), {})
    row = _row(report, 'Groceries')
    assert [c["month"] for c in row["cells"]] == MONTHS
    assert [c["spent"] for c in row["cells"]] == [0.0, 120.0, 0.0, 0.0, 80.0, 0.0]
    assert row["total"] == 200.0


def test_boundary_equal_and_zero_spend_grade_hit():
    report = build_budget_report(
        MONTHS, _spend(1, 'Gas', {'2026-01': 100.0, '2026-03': 100.01}),
        {1: 100})
    verdicts = [c["verdict"] for c in _row(report, 'Gas')["cells"]]
    # Exactly-on-budget is a hit, zero-spend months are hits, over is a miss.
    assert verdicts == ['hit', 'hit', 'miss', 'hit', 'hit', 'hit']


def test_ungraded_row_no_verdicts_no_streak_trend_present():
    report = build_budget_report(
        MONTHS, _spend(1, 'Hobbies', {'2026-04': 50.0}), {})
    row = _row(report, 'Hobbies')
    assert row["budget"] is None
    assert all(c["verdict"] is None for c in row["cells"])
    assert row["streak"] is None
    assert row["trend"]["direction"] in ('rising', 'easing', 'steady')


def test_streak_counts_consecutive_recent_months():
    # miss, then five hits -> "5 mo under" (the old miss doesn't reach back).
    report = build_budget_report(
        MONTHS,
        _spend(1, 'Dining', {'2026-01': 200.0, '2026-02': 50.0, '2026-03': 50.0,
                             '2026-04': 50.0, '2026-05': 50.0, '2026-06': 50.0}),
        {1: 100})
    assert _row(report, 'Dining')["streak"] == {"kind": "under", "length": 5}


def test_streak_over_and_full_window():
    report = build_budget_report(
        MONTHS,
        _spend(1, 'Dining', {'2026-05': 150.0, '2026-06': 160.0}) +
        _spend(2, 'Rent', {m: 999.0 for m in MONTHS}),
        {1: 100, 2: 500})
    # Dining: four zero-hit months then two misses -> 2 mo over.
    assert _row(report, 'Dining')["streak"] == {"kind": "over", "length": 2}
    # Rent: over in all six -> the full window.
    assert _row(report, 'Rent')["streak"] == {"kind": "over", "length": 6}


def test_trend_thresholds():
    def direction(recent_each):
        report = build_budget_report(
            MONTHS,
            _spend(1, 'X', {'2026-01': 100.0, '2026-02': 100.0, '2026-03': 100.0,
                            '2026-04': recent_each, '2026-05': recent_each,
                            '2026-06': recent_each}),
            {})
        return _row(report, 'X')["trend"]["direction"]

    assert direction(110.0) == 'steady'   # exactly +10% is steady
    assert direction(111.0) == 'rising'
    assert direction(90.0) == 'steady'    # exactly -10% is steady
    assert direction(89.0) == 'easing'


def test_trend_zero_halves():
    # All spend in the recent half: prior avg 0 -> rising.
    report = build_budget_report(MONTHS, _spend(1, 'New', {'2026-06': 60.0}), {})
    assert _row(report, 'New')["trend"]["direction"] == 'rising'
    # All spend in the prior half: recent avg 0 -> easing.
    report = build_budget_report(MONTHS, _spend(1, 'Old', {'2026-01': 60.0}), {})
    assert _row(report, 'Old')["trend"]["direction"] == 'easing'
    # Degenerate zero-total row: both halves 0 -> steady, and grades all hits.
    report = build_budget_report(MONTHS, _spend(1, 'Zero', {'2026-03': 0.0}), {1: 50})
    assert _row(report, 'Zero')["trend"]["direction"] == 'steady'
    assert _row(report, 'Zero')["streak"] == {"kind": "under", "length": 6}


def test_ordering_graded_first_then_total_desc_name_tiebreak():
    report = build_budget_report(
        MONTHS,
        _spend(1, 'small-graded', {'2026-06': 10.0}) +
        _spend(2, 'big-ungraded', {'2026-06': 900.0}) +
        _spend(3, 'big-graded', {'2026-06': 500.0}) +
        _spend(4, 'bbb-tie', {'2026-06': 900.0}) +
        _spend(5, 'aaa-tie', {'2026-06': 900.0}),
        {1: 100, 3: 100})
    names = [r["name"] for r in report["rows"]]
    assert names == ['big-graded', 'small-graded',   # graded block, total desc
                     'aaa-tie', 'bbb-tie', 'big-ungraded']  # then ungraded


def test_dormant_budget_dropped_and_empty_input():
    report = build_budget_report(MONTHS, [], {42: 300})
    assert report["rows"] == []
    report = build_budget_report(
        MONTHS, _spend(1, 'Active', {'2026-06': 5.0}), {1: 100, 42: 300})
    assert [r["name"] for r in report["rows"]] == ['Active']


def test_month_labels_short_names_across_year_boundary():
    report = build_budget_report(
        ['2025-08', '2025-09', '2025-10', '2025-11', '2025-12', '2026-01'], [], {})
    assert report["month_labels"] == ['Aug', 'Sep', 'Oct', 'Nov', 'Dec', 'Jan']


# --- load_budget_report (DB-backed) ------------------------------------------

def test_window_excludes_current_month_and_seventh_month_back(users):
    a = users["a"]
    months = _report_months()
    seventh_back = recent_months(8)[7]  # one month older than the window
    create_transaction(a["id"], a["account_id"], 111.0, date.today().isoformat(),
                       category_id=a["category_id"])
    create_transaction(a["id"], a["account_id"], 222.0, f"{months[-1]}-15",
                       category_id=a["category_id"])
    create_transaction(a["id"], a["account_id"], 333.0, f"{seventh_back}-15",
                       category_id=a["category_id"])
    report = load_budget_report(a["id"])
    row = _row(report, 'cat-A')
    assert row["total"] == 222.0
    assert row["cells"][-1]["spent"] == 222.0


def test_excludes_income_adjustments_and_transfers(users):
    a = users["a"]
    month = _report_months()[-1]
    create_transaction(a["id"], a["account_id"], 50.0, f"{month}-10",
                       category_id=a["category_id"])
    create_transaction(a["id"], a["account_id"], 500.0, f"{month}-11",
                       transaction_type="income", category_id=a["category_id"])
    create_transaction(a["id"], a["account_id"], 70.0, f"{month}-12",
                       category_id=a["category_id"], is_adjustment=True)
    create_transfer(a["id"], a["account_id"], a["account_id"], 90.0, f"{month}-13")
    report = load_budget_report(a["id"])
    assert _row(report, 'cat-A')["total"] == 50.0


def test_user_isolation(users):
    a, b = users["a"], users["b"]
    month = _report_months()[-1]
    create_transaction(b["id"], b["account_id"], 640.0, f"{month}-15",
                       category_id=b["category_id"])
    report = load_budget_report(a["id"])
    assert _row(report, 'cat-B') is None
    assert all(r["total"] != 640.0 for r in report["rows"])


def test_graded_vs_ungraded_rows_from_db(users):
    a = users["a"]
    month = _report_months()[-1]
    other_cid = create_category(a["id"], "unbudgeted-cat")
    create_transaction(a["id"], a["account_id"], 80.0, f"{month}-15",
                       category_id=a["category_id"])
    create_transaction(a["id"], a["account_id"], 30.0, f"{month}-16",
                       category_id=other_cid)
    create_budget(a["id"], a["category_id"], 100)
    report = load_budget_report(a["id"])
    graded, ungraded = _row(report, 'cat-A'), _row(report, 'unbudgeted-cat')
    assert graded["budget"] == 100.0
    assert graded["cells"][-1]["verdict"] == 'hit'
    assert graded["streak"] == {"kind": "under", "length": 6}
    assert ungraded["budget"] is None and ungraded["streak"] is None


# --- /budgets route ----------------------------------------------------------

def test_budgets_page_renders_report_section(client_a, users):
    a = users["a"]
    month = _report_months()[-1]
    create_transaction(a["id"], a["account_id"], 80.0, f"{month}-15",
                       category_id=a["category_id"])
    create_budget(a["id"], a["category_id"], 100)
    body = client_a.get("/budgets").get_data(as_text=True)
    assert "Budget report" in body
    assert "mo under" in body
    assert "current budget" in body  # the honesty label


def test_report_hidden_without_window_spend(client_a):
    # The fixture's only transaction is dated today — outside the window.
    body = client_a.get("/budgets").get_data(as_text=True)
    assert "Budget report" not in body


def test_budgets_anon_redirects(anon_client):
    assert anon_client.get("/budgets").status_code == 302
