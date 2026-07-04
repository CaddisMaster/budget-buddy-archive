"""v10.9 tests — Safe to spend (the dashboard hero band).

The figure is fully deterministic (no AI, no cache):

    safe_to_spend = liquid_balance − upcoming_bills
                    − upcoming_transfers_out − budget_room

Pure tests cover the occurrence walkers (window semantics: start exclusive,
end inclusive); DB-backed tests cover compute_safe_to_spend()'s term
definitions — liquid = Bank Account + Debit Card only, bills only on liquid
accounts in UNbudgeted categories (the dedupe), transfers only liquid →
non-liquid, budget_room = positive remainings for the current calendar
month — plus window fallback, isolation, and the dashboard band.

Note: the `users` fixture seeds each user an account of type 'bank' (not a
VALID_ACCOUNT_TYPE), so fixture data never enters the liquid balance and a
freshly-seeded user computes to all zeros.
"""
import calendar
from datetime import date, timedelta

from app.blueprints.main import (
    _advance_past,
    compute_safe_to_spend,
    upcoming_occurrences,
)
from tests.conftest import (
    create_account,
    create_budget,
    create_category,
    create_schedule,
    create_transaction,
    create_transfer_schedule,
)


def _month_end(d):
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


# --- occurrence walkers (pure, no DB) ----------------------------------------

def test_advance_past_walks_stale_next_due():
    # Monthly anchor months in the past walks to the first occurrence after `day`.
    occ = _advance_past(date(2026, 1, 5), "monthly", None, None, date(2026, 3, 10))
    assert occ == date(2026, 4, 5)


def test_advance_past_future_occurrence_untouched():
    occ = _advance_past(date(2026, 3, 20), "monthly", None, None, date(2026, 3, 10))
    assert occ == date(2026, 3, 20)


def test_occurrences_multiple_in_window():
    # Weekly bill, 22-day window → four occurrences.
    occs = upcoming_occurrences(date(2026, 3, 2), "weekly", None, None,
                                date(2026, 3, 1), date(2026, 3, 23))
    assert occs == [date(2026, 3, 2), date(2026, 3, 9),
                    date(2026, 3, 16), date(2026, 3, 23)]


def test_occurrences_window_end_inclusive():
    # A bill due exactly on payday still comes out of this check.
    occs = upcoming_occurrences(date(2026, 3, 23), "weekly", None, None,
                                date(2026, 3, 1), date(2026, 3, 23))
    assert occs == [date(2026, 3, 23)]
    # One day past the window is out.
    assert upcoming_occurrences(date(2026, 3, 24), "weekly", None, None,
                                date(2026, 3, 1), date(2026, 3, 23)) == []


def test_occurrences_window_start_exclusive():
    # next_due == today was already materialized by the due-runners; only the
    # NEXT cycle counts, and monthly pushes it past this window.
    occs = upcoming_occurrences(date(2026, 3, 1), "monthly", None, None,
                                date(2026, 3, 1), date(2026, 3, 15))
    assert occs == []


def test_occurrences_semimonthly_across_month_boundary():
    occs = upcoming_occurrences(date(2026, 3, 15), "semimonthly", 15, 31,
                                date(2026, 3, 1), date(2026, 4, 20))
    assert occs == [date(2026, 3, 15), date(2026, 3, 31), date(2026, 4, 15)]


def test_occurrences_empty_when_beyond_window():
    assert upcoming_occurrences(date(2026, 6, 1), "monthly", None, None,
                                date(2026, 3, 1), date(2026, 3, 31)) == []


# --- compute_safe_to_spend (DB-backed) ---------------------------------------

def test_basic_figure_and_breakdown(users):
    a = users["a"]["id"]
    today = date.today()
    acct = create_account(a, "Checking", "Bank Account")
    create_transaction(a, acct, 3000, today, "income")
    create_transaction(a, acct, 500, today, "expense")
    cat = create_category(a, "Groceries")
    create_budget(a, cat, 400)
    create_transaction(a, acct, 150, today, "expense", category_id=cat)
    # Paycheck in 15 days pins the window.
    create_schedule(a, acct, 2000, "monthly", today + timedelta(days=15),
                    transaction_type="income")
    # Unbudgeted bill + a transfer to the credit card, both inside the window.
    create_schedule(a, acct, 200, "monthly", today + timedelta(days=5))
    credit = create_account(a, "Visa", "Credit Card")
    create_transfer_schedule(a, acct, credit, 100, "monthly",
                             today + timedelta(days=7))

    safe = compute_safe_to_spend(a, today=today)
    assert safe["liquid_balance"] == 2350.0       # 3000 − 500 − 150
    assert safe["upcoming_bills"] == 200.0
    assert safe["upcoming_transfers_out"] == 100.0
    assert safe["budget_room"] == 250.0           # 400 − 150
    assert safe["safe_to_spend"] == 1800.0
    assert safe["window_end"] == today + timedelta(days=15)
    assert safe["window_is_paycheck"] is True
    assert [i["due"] for i in safe["bill_items"]] == [today + timedelta(days=5)]
    assert safe["bill_items"][0]["amount"] == 200.0
    assert [i["due"] for i in safe["transfer_items"]] == [today + timedelta(days=7)]


def test_credit_card_balance_excluded_from_liquid(users):
    a = users["a"]["id"]
    today = date.today()
    credit = create_account(a, "Visa", "Credit Card")
    create_transaction(a, credit, 1000, today, "income")
    checking = create_account(a, "Checking", "Bank Account")
    create_transaction(a, checking, 100, today, "income")

    safe = compute_safe_to_spend(a, today=today)
    assert safe["liquid_balance"] == 100.0


def test_debit_card_counts_as_liquid(users):
    a = users["a"]["id"]
    today = date.today()
    debit = create_account(a, "Debit", "Debit Card")
    create_transaction(a, debit, 75, today, "income")

    safe = compute_safe_to_spend(a, today=today)
    assert safe["liquid_balance"] == 75.0


def test_bill_on_credit_card_account_excluded(users):
    a = users["a"]["id"]
    today = date.today()
    checking = create_account(a, "Checking", "Bank Account")
    credit = create_account(a, "Visa", "Credit Card")
    # Paycheck in 20 days pins the window (avoids month-end edge sensitivity).
    create_schedule(a, checking, 1000, "monthly", today + timedelta(days=20),
                    transaction_type="income")
    create_schedule(a, credit, 60, "monthly", today + timedelta(days=3))

    safe = compute_safe_to_spend(a, today=today)
    assert safe["upcoming_bills"] == 0.0
    assert safe["bill_items"] == []


def test_budgeted_category_bill_deduped(users):
    a = users["a"]["id"]
    today = date.today()
    acct = create_account(a, "Checking", "Bank Account")
    create_schedule(a, acct, 1000, "monthly", today + timedelta(days=20),
                    transaction_type="income")
    cat = create_category(a, "Rent")
    create_budget(a, cat, 300)
    # The budgeted-category bill is covered by budget_room, not double-counted.
    create_schedule(a, acct, 300, "monthly", today + timedelta(days=5),
                    category_id=cat)
    # The same bill without a category counts.
    create_schedule(a, acct, 75, "monthly", today + timedelta(days=5))

    safe = compute_safe_to_spend(a, today=today)
    assert safe["upcoming_bills"] == 75.0
    assert len(safe["bill_items"]) == 1
    assert safe["budget_room"] == 300.0


def test_transfer_liquidity_rules(users):
    a = users["a"]["id"]
    today = date.today()
    checking = create_account(a, "Checking", "Bank Account")
    savings = create_account(a, "Savings", "Debit Card")
    credit = create_account(a, "Visa", "Credit Card")
    create_schedule(a, checking, 1000, "monthly", today + timedelta(days=20),
                    transaction_type="income")
    # liquid → liquid nets to zero inside liquid_balance: not counted.
    create_transfer_schedule(a, checking, savings, 50, "monthly",
                             today + timedelta(days=5))
    # liquid → credit drains spendable cash: counted.
    create_transfer_schedule(a, checking, credit, 80, "monthly",
                             today + timedelta(days=5))
    # non-liquid → liquid is incoming: not counted.
    create_transfer_schedule(a, credit, checking, 60, "monthly",
                             today + timedelta(days=5))

    safe = compute_safe_to_spend(a, today=today)
    assert safe["upcoming_transfers_out"] == 80.0
    assert len(safe["transfer_items"]) == 1
    assert safe["transfer_items"][0]["amount"] == 80.0


def test_window_is_next_paycheck_inclusive(users):
    a = users["a"]["id"]
    today = date.today()
    acct = create_account(a, "Checking", "Bank Account")
    create_schedule(a, acct, 1000, "monthly", today + timedelta(days=10),
                    transaction_type="income")
    create_schedule(a, acct, 900, "monthly", today + timedelta(days=18),
                    transaction_type="income")  # later paycheck doesn't shrink it
    create_schedule(a, acct, 40, "monthly", today + timedelta(days=9))
    create_schedule(a, acct, 50, "monthly", today + timedelta(days=10))  # on payday: in
    create_schedule(a, acct, 60, "monthly", today + timedelta(days=11))  # past: out

    safe = compute_safe_to_spend(a, today=today)
    assert safe["window_end"] == today + timedelta(days=10)
    assert safe["window_is_paycheck"] is True
    assert safe["upcoming_bills"] == 90.0


def test_window_falls_back_to_month_end(users):
    a = users["a"]["id"]
    today = date.today()
    safe = compute_safe_to_spend(a, today=today)
    assert safe["window_end"] == _month_end(today)
    assert safe["window_is_paycheck"] is False


def test_paycheck_due_today_walks_to_next_occurrence(users):
    # next_due == today was materialized by the due-runners; the window must
    # never collapse to zero on payday.
    a = users["a"]["id"]
    today = date.today()
    acct = create_account(a, "Checking", "Bank Account")
    create_schedule(a, acct, 1000, "monthly", today, transaction_type="income")

    safe = compute_safe_to_spend(a, today=today)
    assert safe["window_end"] > today
    assert safe["window_is_paycheck"] is True


def test_inactive_schedules_ignored(users):
    a = users["a"]["id"]
    today = date.today()
    acct = create_account(a, "Checking", "Bank Account")
    create_schedule(a, acct, 1000, "monthly", today + timedelta(days=5),
                    transaction_type="income", is_active=False)
    create_schedule(a, acct, 200, "monthly", today + timedelta(days=2),
                    is_active=False)
    credit = create_account(a, "Visa", "Credit Card")
    create_transfer_schedule(a, acct, credit, 90, "monthly",
                             today + timedelta(days=2), is_active=False)

    safe = compute_safe_to_spend(a, today=today)
    assert safe["window_is_paycheck"] is False   # inactive income doesn't pin it
    assert safe["upcoming_bills"] == 0.0
    assert safe["upcoming_transfers_out"] == 0.0


def test_budget_room_counts_positive_remainings_only(users):
    a = users["a"]["id"]
    today = date.today()
    acct = create_account(a, "Checking", "Bank Account")
    over = create_category(a, "Dining")
    create_budget(a, over, 100)
    create_transaction(a, acct, 250, today, "expense", category_id=over)
    under = create_category(a, "Fuel")
    create_budget(a, under, 200)
    create_transaction(a, acct, 50, today, "expense", category_id=under)

    safe = compute_safe_to_spend(a, today=today)
    assert safe["budget_room"] == 150.0          # overspent Dining contributes 0


def test_negative_result_not_clamped(users):
    a = users["a"]["id"]
    today = date.today()
    acct = create_account(a, "Checking", "Bank Account")
    create_transaction(a, acct, 50, today, "income")
    cat = create_category(a, "Everything")
    create_budget(a, cat, 500)

    safe = compute_safe_to_spend(a, today=today)
    assert safe["safe_to_spend"] == -450.0


def test_fresh_user_all_zeros(users):
    # Only the fixture seeds exist (a 'bank'-type account — not liquid).
    a = users["a"]["id"]
    today = date.today()
    safe = compute_safe_to_spend(a, today=today)
    assert safe["liquid_balance"] == 0.0
    assert safe["upcoming_bills"] == 0.0
    assert safe["upcoming_transfers_out"] == 0.0
    assert safe["budget_room"] == 0.0
    assert safe["safe_to_spend"] == 0.0
    assert safe["bill_items"] == [] and safe["transfer_items"] == []


def test_user_isolation(users):
    # B's money, schedules, transfers, and budgets never move A's figure.
    a, b = users["a"]["id"], users["b"]["id"]
    today = date.today()
    b_acct = create_account(b, "B-Checking", "Bank Account")
    create_transaction(b, b_acct, 5000, today, "income")
    create_schedule(b, b_acct, 1000, "monthly", today + timedelta(days=5),
                    transaction_type="income")
    create_schedule(b, b_acct, 200, "monthly", today + timedelta(days=2))
    b_credit = create_account(b, "B-Visa", "Credit Card")
    create_transfer_schedule(b, b_acct, b_credit, 90, "monthly",
                             today + timedelta(days=2))
    b_cat = create_category(b, "B-Stuff")
    create_budget(b, b_cat, 400)

    safe = compute_safe_to_spend(a, today=today)
    assert safe["safe_to_spend"] == 0.0
    assert safe["window_is_paycheck"] is False   # B's paycheck isn't A's window


def test_weekly_bill_counts_every_occurrence_before_payday(users):
    a = users["a"]["id"]
    today = date.today()
    acct = create_account(a, "Checking", "Bank Account")
    create_schedule(a, acct, 1000, "monthly", today + timedelta(days=22),
                    transaction_type="income")
    create_schedule(a, acct, 25, "weekly", today + timedelta(days=1))

    safe = compute_safe_to_spend(a, today=today)
    assert safe["upcoming_bills"] == 100.0       # days +1, +8, +15, +22
    assert len(safe["bill_items"]) == 4


# --- dashboard band (route) ---------------------------------------------------

def test_dashboard_renders_safe_band(client_a):
    resp = client_a.get("/dashboard")
    assert resp.status_code == 200
    assert b"hero-safe" in resp.data
    assert b"Safe to spend" in resp.data
    assert b"How this is calculated" in resp.data


def test_dashboard_band_independent_of_month_filter(client_a, users):
    # Filter the charts to a past month with data — the band still shows the
    # figure for NOW (month-end fallback window of the CURRENT month).
    a = users["a"]["id"]
    today = date.today()
    past = (today.replace(day=1) - timedelta(days=1)).replace(day=15)
    for _ in range(5):
        past = (past.replace(day=1) - timedelta(days=1)).replace(day=15)
    acct = users["a"]["account_id"]
    cat = users["a"]["category_id"]
    create_transaction(a, acct, 80, past, "expense", category_id=cat)

    resp = client_a.get(f"/dashboard?month={past.year}-{past.month:02d}")
    assert resp.status_code == 200
    assert b"hero-safe" in resp.data
    until = _month_end(today).strftime("until %b %-d (month end)")
    assert until.encode() in resp.data
