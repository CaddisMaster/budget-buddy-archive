"""Shared amount validation (parse_positive_amount).

float() parses 'nan' and 'inf', and neither trips an `<= 0` check — but a NaN
stored in a numeric column poisons every SUM() the dashboards aggregate. The
helper rejects non-finite values along with non-numbers and non-positives, and
every amount-taking form routes through it. Pure tests cover the helper; route
tests prove a 'nan' post writes nothing to any of the money tables.
"""
from app.db import get_db_connection
from app.helpers import parse_positive_amount, parse_signed_amount
from tests.conftest import (
    count_transactions_like,
    count_transfer_schedules,
    fetch_budget_by_category,
    fetch_transaction,
)

HX = {"HX-Request": "true"}


def _count(table, user_id):
    """Row count for one of the money tables, straight from the DB."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = %s", (user_id,))
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    return n


# --- parse_positive_amount (pure) --------------------------------------------

def test_valid_amount_parses():
    assert parse_positive_amount("42.50") == (42.5, None)


def test_whitespace_is_stripped():
    assert parse_positive_amount("  10 ") == (10.0, None)


def test_empty_and_none_are_required():
    assert parse_positive_amount("") == (None, "Amount is required")
    assert parse_positive_amount(None) == (None, "Amount is required")


def test_non_number_rejected():
    assert parse_positive_amount("abc") == (None, "Amount must be a valid number")


def test_non_finite_rejected():
    # float() accepts all of these; none may reach the DB (NaN poisons SUM()).
    for raw in ("nan", "NaN", "inf", "-inf", "Infinity", "-Infinity"):
        assert parse_positive_amount(raw) == (
            None, "Amount must be a valid number"), raw


def test_zero_and_negative_rejected():
    assert parse_positive_amount("0") == (None, "Amount must be greater than zero")
    assert parse_positive_amount("-5") == (None, "Amount must be greater than zero")


def test_label_customizes_messages():
    assert parse_positive_amount("", label="Target amount") == (
        None, "Target amount is required")
    assert parse_positive_amount("nan", label="Target amount") == (
        None, "Target amount must be a valid number")


# --- parse_signed_amount (pure) -----------------------------------------------
# v10.9 balance check-in: a bank balance is signed (credit cards are negative)
# and may be exactly zero, so the strictly-positive check doesn't apply — but
# the NaN/inf guard absolutely still does.

def test_signed_accepts_negative_and_zero():
    assert parse_signed_amount("-512.10") == (-512.1, None)
    assert parse_signed_amount("0") == (0.0, None)
    assert parse_signed_amount("  42.50 ") == (42.5, None)


def test_signed_empty_and_none_are_required():
    assert parse_signed_amount("") == (None, "Amount is required")
    assert parse_signed_amount(None) == (None, "Amount is required")


def test_signed_non_number_and_non_finite_rejected():
    assert parse_signed_amount("abc") == (None, "Amount must be a valid number")
    for raw in ("nan", "NaN", "inf", "-inf", "Infinity", "-Infinity"):
        assert parse_signed_amount(raw) == (
            None, "Amount must be a valid number"), raw


def test_signed_label_customizes_messages():
    assert parse_signed_amount("", label="Bank balance") == (
        None, "Bank balance is required")
    assert parse_signed_amount("nan", label="Bank balance") == (
        None, "Bank balance must be a valid number")


# --- every amount form rejects a NaN post ------------------------------------

def test_new_transaction_rejects_nan(client_a, users):
    uid = users["a"]["id"]
    resp = client_a.post("/transactions/new", data={
        "amount": "nan",
        "description": "nan-desc",
        "transaction_date": "2026-07-01",
        "account_id": users["a"]["account_id"],
        "transaction_type": "expense",
    })
    assert resp.status_code == 302  # back to the form with a flash, no 500
    assert count_transactions_like(uid, "nan-desc") == 0


def test_edit_transaction_rejects_nan(client_a, users):
    tid = users["a"]["transaction_id"]
    resp = client_a.post(f"/transactions/{tid}/edit", headers=HX, data={
        "amount": "nan",
        "description": "poisoned",
        "transaction_date": "2026-07-01",
        "account_id": users["a"]["account_id"],
        "transaction_type": "expense",
    })
    assert resp.status_code == 200  # edit-row fragment with the error
    amount, description, _ = fetch_transaction(tid)
    assert float(amount) == 42.50  # seeded value untouched
    assert description != "poisoned"


def test_set_budget_rejects_nan(client_a, users):
    uid, cid = users["a"]["id"], users["a"]["category_id"]
    resp = client_a.post("/budgets/set", headers=HX,
                         data={"category_id": cid, "amount": "nan"})
    assert resp.status_code == 200  # error toast, no 500
    assert fetch_budget_by_category(uid, cid) is None


def test_create_schedule_rejects_nan(client_a, users):
    uid = users["a"]["id"]
    resp = client_a.post("/scheduled", headers=HX, data={
        "transaction_type": "income",
        "amount": "nan",
        "account_id": users["a"]["account_id"],
        "frequency": "monthly",
        "next_due": "2027-01-01",
    })
    assert resp.status_code == 200
    assert _count("schedules", uid) == 0


def test_create_transfer_rejects_nan(client_a, users):
    uid = users["a"]["id"]
    before = _count("transactions", uid)
    resp = client_a.post("/transfers", data={
        "from_account": users["a"]["account_id"],
        "to_account": users["a"]["account_id"],  # rejected on amount first anyway
        "amount": "nan",
        "transfer_date": "2026-07-01",
    })
    assert resp.status_code == 302
    assert _count("transactions", uid) == before  # no legs inserted


def test_create_transfer_schedule_rejects_nan(client_a, users):
    uid = users["a"]["id"]
    resp = client_a.post("/transfers/recurring", headers=HX, data={
        "from_account": users["a"]["account_id"],
        "to_account": users["a"]["account_id"],
        "amount": "nan",
        "frequency": "monthly",
        "next_due": "2027-01-01",
    })
    assert resp.status_code == 200
    assert count_transfer_schedules(uid) == 0


def test_create_goal_rejects_nan(client_a, users):
    uid = users["a"]["id"]
    resp = client_a.post("/goals", headers=HX, data={
        "name": "nan goal",
        "target_amount": "nan",
        "account_id": users["a"]["account_id"],
    })
    assert resp.status_code == 200
    assert _count("goals", uid) == 0
