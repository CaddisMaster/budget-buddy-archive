"""v10.4 Auto-Transfer — recurring transfers (the transfer-tab twin of Scheduled).

Covers the run_due_transfers generator (materializes a PAIRED transfer, catch-up,
active/future gates, user isolation) and the inline-CRUD routes (fragment shape,
validation, ownership 404s). Route tests hit the dev DB via the shared fixtures;
CSRF + rate limiter are disabled under test.
"""
from datetime import date, timedelta

from app.db import get_db_connection
from app.blueprints.transfers import run_due_transfers
from tests.conftest import (
    create_account,
    create_transfer_schedule,
    fetch_transfer_schedule,
    count_transfer_schedules,
    count_transactions_like,
)

HX = {"HX-Request": "true"}
LABEL = "seed-auto-transfer"


def _second_account(user):
    """Every user fixture has one account; transfers need a distinct second."""
    return create_account(user["id"], "acct-2")


def _transfer_legs(user_id, description):
    """Return the (account_id, transaction_type, is_transfer, transfer_group_id)
    legs a recurring transfer materialized, newest first."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT account_id, transaction_type, is_transfer, transfer_group_id "
        "FROM transactions WHERE user_id = %s AND description = %s "
        "ORDER BY id",
        (user_id, description),
    )
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows


# --- run_due_transfers ------------------------------------------------------

def test_due_transfer_materializes_a_pair_and_advances(users):
    a = users["a"]
    to_acct = _second_account(a)
    yesterday = date.today() - timedelta(days=1)
    tsid = create_transfer_schedule(a["id"], a["account_id"], to_acct, 200,
                                    "monthly", yesterday)
    run_due_transfers(a["id"])

    legs = _transfer_legs(a["id"], LABEL)
    assert len(legs) == 2                       # one occurrence → expense + income
    assert all(leg[2] is True for leg in legs)  # both is_transfer
    assert legs[0][3] == legs[1][3]             # share one transfer_group_id
    by_type = {leg[1]: leg[0] for leg in legs}
    assert by_type["expense"] == a["account_id"]  # out of the From account
    assert by_type["income"] == to_acct           # into the To account

    # next_due advanced into the future, so a re-run doesn't duplicate.
    assert fetch_transfer_schedule(tsid)[2] > date.today()
    run_due_transfers(a["id"])
    assert len(_transfer_legs(a["id"], LABEL)) == 2


def test_far_behind_transfer_catches_up(users):
    a = users["a"]
    to_acct = _second_account(a)
    three_weeks_ago = date.today() - timedelta(weeks=3)
    create_transfer_schedule(a["id"], a["account_id"], to_acct, 20, "weekly",
                             three_weeks_ago)
    run_due_transfers(a["id"])
    # Occurrences at -21, -14, -7, today → 4 transfers → 8 legs.
    assert len(_transfer_legs(a["id"], LABEL)) == 8


def test_future_transfer_generates_nothing(users):
    a = users["a"]
    to_acct = _second_account(a)
    next_week = date.today() + timedelta(days=7)
    create_transfer_schedule(a["id"], a["account_id"], to_acct, 20, "weekly",
                             next_week)
    run_due_transfers(a["id"])
    assert len(_transfer_legs(a["id"], LABEL)) == 0


def test_inactive_transfer_generates_nothing(users):
    a = users["a"]
    to_acct = _second_account(a)
    yesterday = date.today() - timedelta(days=1)
    create_transfer_schedule(a["id"], a["account_id"], to_acct, 20, "monthly",
                             yesterday, is_active=False)
    run_due_transfers(a["id"])
    assert len(_transfer_legs(a["id"], LABEL)) == 0


def test_run_due_transfers_is_user_scoped(users):
    a, b = users["a"], users["b"]
    to_acct = _second_account(a)
    yesterday = date.today() - timedelta(days=1)
    create_transfer_schedule(a["id"], a["account_id"], to_acct, 20, "monthly",
                             yesterday)
    run_due_transfers(b["id"])  # B's run must not fire A's transfer
    assert len(_transfer_legs(a["id"], LABEL)) == 0
    run_due_transfers(a["id"])
    assert len(_transfer_legs(a["id"], LABEL)) == 2


# --- inline CRUD routes -----------------------------------------------------

def test_create_transfer_schedule_returns_row_fragment(client_a, users):
    a = users["a"]
    to_acct = _second_account(a)
    resp = client_a.post("/transfers/recurring", headers=HX, data={
        "from_account": a["account_id"],
        "to_account": to_acct,
        "amount": "200",
        "description": "To savings",
        "frequency": "monthly",
        "next_due": (date.today() + timedelta(days=3)).isoformat(),
    })
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "<html" not in body.lower()
    assert "transfer-schedule-" in body
    assert count_transfer_schedules(a["id"]) == 1


def test_create_semimonthly_transfer_computes_next_due(client_a, users):
    a = users["a"]
    to_acct = _second_account(a)
    resp = client_a.post("/transfers/recurring", headers=HX, data={
        "from_account": a["account_id"],
        "to_account": to_acct,
        "amount": "100",
        "frequency": "semimonthly",
        "anchor_day": "1",
        "second_day": "15",
    })
    assert resp.status_code == 200
    assert count_transfer_schedules(a["id"]) == 1
    # The server computed a non-past next_due from the two days.
    assert fetch_transfer_schedule(_only_id(a["id"]))[2] >= date.today()


def _only_id(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM transfer_schedules WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    cur.close(); conn.close()
    return row[0]


def test_create_transfer_schedule_rejects_same_account(client_a, users):
    a = users["a"]
    resp = client_a.post("/transfers/recurring", headers=HX, data={
        "from_account": a["account_id"],
        "to_account": a["account_id"],   # same as From → rejected
        "amount": "50",
        "frequency": "monthly",
        "next_due": (date.today() + timedelta(days=3)).isoformat(),
    })
    assert resp.status_code == 200
    assert count_transfer_schedules(a["id"]) == 0


def test_create_transfer_schedule_rejects_unowned_account(client_a, users):
    a, b = users["a"], users["b"]
    resp = client_a.post("/transfers/recurring", headers=HX, data={
        "from_account": a["account_id"],
        "to_account": b["account_id"],   # B's account → rejected (write-side IDOR)
        "amount": "50",
        "frequency": "monthly",
        "next_due": (date.today() + timedelta(days=3)).isoformat(),
    })
    assert resp.status_code == 200
    assert count_transfer_schedules(a["id"]) == 0


def test_create_transfer_schedule_rejects_past_date(client_a, users):
    a = users["a"]
    to_acct = _second_account(a)
    resp = client_a.post("/transfers/recurring", headers=HX, data={
        "from_account": a["account_id"],
        "to_account": to_acct,
        "amount": "30",
        "frequency": "monthly",
        "next_due": (date.today() - timedelta(days=2)).isoformat(),
    })
    assert resp.status_code == 200
    assert count_transfer_schedules(a["id"]) == 0


def test_edit_then_delete_transfer_schedule(client_a, users):
    a = users["a"]
    to_acct = _second_account(a)
    tsid = create_transfer_schedule(a["id"], a["account_id"], to_acct, 40,
                                    "monthly", date.today() + timedelta(days=5))
    # Edit form fragment.
    resp = client_a.get(f"/transfers/recurring/{tsid}/edit", headers=HX)
    assert resp.status_code == 200
    assert "<html" not in resp.data.decode().lower()
    # Save an update.
    resp = client_a.post(f"/transfers/recurring/{tsid}/edit", headers=HX, data={
        "from_account": a["account_id"],
        "to_account": to_acct,
        "amount": "99",
        "frequency": "monthly",
        "next_due": (date.today() + timedelta(days=5)).isoformat(),
    })
    assert resp.status_code == 200
    assert float(fetch_transfer_schedule(tsid)[0]) == 99
    # Delete.
    resp = client_a.delete(f"/transfers/recurring/{tsid}", headers=HX)
    assert resp.status_code == 200
    assert count_transfer_schedules(a["id"]) == 0


def test_cannot_edit_or_delete_other_users_transfer_schedule(client_a, users):
    b = users["b"]
    b_to = _second_account(b)
    b_tsid = create_transfer_schedule(b["id"], b["account_id"], b_to, 10,
                                      "monthly", date.today() + timedelta(days=5))
    assert client_a.get(f"/transfers/recurring/{b_tsid}/edit", headers=HX).status_code == 404
    assert client_a.get(f"/transfers/recurring/{b_tsid}/row", headers=HX).status_code == 404
    assert client_a.delete(f"/transfers/recurring/{b_tsid}", headers=HX).status_code == 404
    # B's transfer untouched.
    assert fetch_transfer_schedule(b_tsid) is not None
