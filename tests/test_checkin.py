"""v10.9 Balance check-in — per-account reconciliation on /accounts.

You type what the bank actually says; the route recomputes the app's balance
server-side, inserts the ONE adjustment transaction that closes the gap
(is_adjustment: counts toward balance, excluded from analytics), and stamps
account.last_checked_in. A matching balance still counts as a check-in (stamp,
no transaction). Non-AI — no seams to mock.
"""
from datetime import date

from app.blueprints.insights import compute_month_facts
from app.db import get_db_connection
from tests.conftest import (
    account_balance,
    create_account,
    create_transaction,
)

HX = {"HX-Request": "true"}
DESC = "Balance check-in"


def _adjustment_rows(user_id):
    """All 'Balance check-in' rows for a user:
    (amount, transaction_type, category_id, is_adjustment, transaction_date)."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT amount, transaction_type, category_id, is_adjustment, "
        "transaction_date FROM transactions "
        "WHERE user_id = %s AND description = %s",
        (user_id, DESC),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def _last_checked_in(account_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT last_checked_in FROM account WHERE account_id = %s",
        (account_id,),
    )
    val = cur.fetchone()[0]
    cur.close()
    conn.close()
    return val


# --- auth + isolation ---------------------------------------------------------

def test_checkin_requires_login(anon_client, users):
    aid = users["a"]["account_id"]
    assert anon_client.get(f"/accounts/{aid}/checkin").status_code == 302
    assert anon_client.post(f"/accounts/{aid}/checkin",
                            data={"actual_balance": "10"}).status_code == 302


def test_checkin_other_user_account_404(client_a, users):
    bid = users["b"]["account_id"]
    assert client_a.get(f"/accounts/{bid}/checkin", headers=HX).status_code == 404
    resp = client_a.post(f"/accounts/{bid}/checkin", headers=HX,
                         data={"actual_balance": "999"})
    assert resp.status_code == 404
    assert _adjustment_rows(users["b"]["id"]) == []
    assert _last_checked_in(bid) is None


# --- the reconciliation write -------------------------------------------------

def test_gap_up_inserts_income_adjustment(client_a, users):
    uid = users["a"]["id"]
    aid = create_account(uid, "checkin-up")
    create_transaction(uid, aid, 100, date.today(), transaction_type="expense")
    # App says -100; the bank says 50 → a +150 income adjustment.
    resp = client_a.post(f"/accounts/{aid}/checkin", headers=HX,
                         data={"actual_balance": "50"})
    assert resp.status_code == 200
    rows = _adjustment_rows(uid)
    assert len(rows) == 1
    amount, ttype, category_id, is_adjustment, tdate = rows[0]
    assert float(amount) == 150.0
    assert ttype == "income"
    assert category_id is None
    assert is_adjustment is True
    assert tdate == date.today()
    assert account_balance(aid) == 50.0
    assert _last_checked_in(aid) == date.today()


def test_gap_down_inserts_expense_adjustment(client_a, users):
    uid = users["a"]["id"]
    aid = create_account(uid, "checkin-down")
    create_transaction(uid, aid, 200, date.today(), transaction_type="income")
    # App says 200; the bank says 120 → an $80 expense adjustment.
    resp = client_a.post(f"/accounts/{aid}/checkin", headers=HX,
                         data={"actual_balance": "120"})
    assert resp.status_code == 200
    rows = _adjustment_rows(uid)
    assert len(rows) == 1
    assert float(rows[0][0]) == 80.0
    assert rows[0][1] == "expense"
    assert account_balance(aid) == 120.0


def test_negative_bank_balance_credit_card(client_a, users):
    uid = users["a"]["id"]
    aid = create_account(uid, "checkin-visa", account_type="Credit Card")
    create_transaction(uid, aid, 500, date.today(), transaction_type="expense")
    # App says -500; the card actually sits at -450 → +50 income adjustment.
    resp = client_a.post(f"/accounts/{aid}/checkin", headers=HX,
                         data={"actual_balance": "-450"})
    assert resp.status_code == 200
    rows = _adjustment_rows(uid)
    assert len(rows) == 1
    assert float(rows[0][0]) == 50.0
    assert rows[0][1] == "income"
    assert account_balance(aid) == -450.0
    assert _last_checked_in(aid) == date.today()


def test_matching_balance_stamps_without_transaction(client_a, users):
    uid = users["a"]["id"]
    aid = create_account(uid, "checkin-match")
    create_transaction(uid, aid, 75, date.today(), transaction_type="income")
    resp = client_a.post(f"/accounts/{aid}/checkin", headers=HX,
                         data={"actual_balance": "75.00"})
    assert resp.status_code == 200
    assert _adjustment_rows(uid) == []  # nothing to close
    assert _last_checked_in(aid) == date.today()  # but it still counts


def test_invalid_amount_writes_nothing(client_a, users):
    uid = users["a"]["id"]
    aid = create_account(uid, "checkin-bad")
    for raw in ("nan", "inf", "abc", ""):
        resp = client_a.post(f"/accounts/{aid}/checkin", headers=HX,
                             data={"actual_balance": raw})
        assert resp.status_code == 200  # check-in row re-rendered with the error
        assert b"Bank balance" in resp.data, raw
    assert _adjustment_rows(uid) == []
    assert _last_checked_in(aid) is None  # a failed check-in doesn't stamp


# --- the trust property: balance moves, analytics don't ----------------------

def test_adjustment_counts_in_balance_but_not_analytics(client_a, users):
    uid = users["a"]["id"]
    aid = create_account(uid, "checkin-trust")
    today = date.today()
    create_transaction(uid, aid, 300, today, transaction_type="income")
    before = compute_month_facts(uid, today.year, today.month)
    # Bank says 250 → a $50 expense-direction adjustment.
    client_a.post(f"/accounts/{aid}/checkin", headers=HX,
                  data={"actual_balance": "250"})
    assert account_balance(aid) == 250.0  # balance snapped to the bank figure
    after = compute_month_facts(uid, today.year, today.month)
    # ...but the month's analytics figures are untouched (is_adjustment).
    assert after["expenses"] == before["expenses"]
    assert after["income"] == before["income"]


# --- fragments + page ---------------------------------------------------------

def test_checkin_form_fragment_shape(client_a, users):
    aid = users["a"]["account_id"]
    resp = client_a.get(f"/accounts/{aid}/checkin", headers=HX)
    assert resp.status_code == 200
    assert b"<html" not in resp.data
    assert b"actual_balance" in resp.data
    assert b"App says" in resp.data


def test_checkin_post_returns_row_fragment_and_toast(client_a, users):
    uid = users["a"]["id"]
    aid = create_account(uid, "checkin-frag")
    resp = client_a.post(f"/accounts/{aid}/checkin", headers=HX,
                         data={"actual_balance": "0"})
    assert resp.status_code == 200
    assert b"<html" not in resp.data
    assert b"checkin-frag" in resp.data  # the normal row, swapped back in
    assert "showToast" in resp.headers.get("HX-Trigger", "")


def test_accounts_page_shows_last_checkin_column(client_a, users):
    resp = client_a.get("/accounts")
    assert resp.status_code == 200
    assert b"Last check-in" in resp.data
    assert b"Never" in resp.data  # seeded account has never been checked in
