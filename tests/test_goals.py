"""Savings goal tests — progress model + data isolation.

`saved = current account balance − baseline_amount`, so progress is derived from
the linked account and a transfer into it advances the goal with no separate
step. build_goals_view() is exercised directly (it takes a cursor + user_id, no
request context); the 404 ownership guards are exercised through the routes.
"""
from datetime import date

from app.blueprints.goals import build_goals_view
from app.db import get_db_connection
from tests.conftest import (
    create_account,
    create_goal,
    create_transaction,
    create_transfer,
    fetch_goal,
)

TODAY = date.today().isoformat()


def _goal_view(user_id, goal_id):
    conn = get_db_connection()
    cur = conn.cursor()
    view = build_goals_view(cur, user_id)
    cur.close()
    conn.close()
    return next(g for g in view if g["id"] == goal_id)


# --- progress model ---------------------------------------------------------

def test_baseline_zero_counts_existing_balance(users):
    acct = create_account(users["a"]["id"], "acct-A-goal")
    create_transaction(users["a"]["id"], acct, 300, TODAY, transaction_type="income")
    gid = create_goal(users["a"]["id"], acct, 1000, baseline=0)

    g = _goal_view(users["a"]["id"], gid)
    assert g["saved"] == 300.0
    assert g["percent"] == 30.0


def test_baseline_fresh_ignores_existing_balance(users):
    acct = create_account(users["a"]["id"], "acct-A-fresh")
    create_transaction(users["a"]["id"], acct, 300, TODAY, transaction_type="income")
    # "Start fresh" stores baseline = current balance, so saved starts at 0.
    gid = create_goal(users["a"]["id"], acct, 1000, baseline=300)

    g = _goal_view(users["a"]["id"], gid)
    assert g["saved"] == 0.0


def test_transfer_into_linked_account_advances_goal(users):
    src = users["a"]["account_id"]
    dest = create_account(users["a"]["id"], "acct-A-target")
    gid = create_goal(users["a"]["id"], dest, 1000, baseline=0)
    assert _goal_view(users["a"]["id"], gid)["saved"] == 0.0

    create_transfer(users["a"]["id"], src, dest, 200, TODAY)
    assert _goal_view(users["a"]["id"], gid)["saved"] == 200.0


# --- isolation --------------------------------------------------------------

def test_cannot_delete_another_users_goal(client_a, users):
    gid = create_goal(users["b"]["id"], users["b"]["account_id"], 500)
    response = client_a.delete(f"/goals/{gid}", follow_redirects=True)
    assert response.status_code == 404
    assert fetch_goal(gid) is not None  # B's goal survives


def test_cannot_edit_another_users_goal(client_a, users):
    gid = create_goal(users["b"]["id"], users["b"]["account_id"], 500)
    response = client_a.post(
        f"/goals/{gid}/edit",
        data={"name": "hacked", "target_amount": "1",
              "account_id": users["b"]["account_id"]},
        follow_redirects=True,
    )
    assert response.status_code == 404
    name, target, _, owner = fetch_goal(gid)[:4]
    assert name == "seed-goal"            # unchanged
    assert float(target) == 500.0
    assert owner == users["b"]["id"]


def test_missing_goal_returns_404(client_a, users):
    response = client_a.get("/goals/99999999/edit", follow_redirects=True)
    assert response.status_code == 404


# --- payoff goals (v10.9) ----------------------------------------------------
# A payoff goal snapshots at creation: baseline = the (negative) balance,
# target = the starting debt. The shared projection then reads saved as "paid
# off" and remaining as the CURRENT actual debt.

HX = {"HX-Request": "true"}


def _debt_account(users, amount=500):
    """A fresh account carrying `amount` of debt (one expense, negative balance)."""
    acct = create_account(users["a"]["id"], "acct-A-card")
    create_transaction(users["a"]["id"], acct, amount, TODAY,
                       transaction_type="expense")
    return acct


def test_create_payoff_goal_snapshots_debt(client_a, users):
    acct = _debt_account(users, 500)
    resp = client_a.post("/goals", headers=HX, data={
        "goal_type": "payoff",
        "name": "Clear the card",
        "account_id": acct,
        "target_date": "2027-01-01",
        # no target_amount — it's derived, the payoff form doesn't send one
    })
    assert resp.status_code == 200
    assert "Paid off" in resp.data.decode()
    name, target, _, _, goal_type, baseline, _ = fetch_goal(
        find_goal_id(users["a"]["id"], "Clear the card"))
    assert name == "Clear the card"
    assert goal_type == "payoff"
    assert float(target) == 500.0     # starting debt
    assert float(baseline) == -500.0  # balance at creation


def test_payoff_rejected_when_nothing_to_pay_off(client_a, users):
    # An account in the black (and one at exactly $0) has nothing to pay down.
    funded = create_account(users["a"]["id"], "acct-A-funded")
    create_transaction(users["a"]["id"], funded, 100, TODAY,
                       transaction_type="income")
    empty = create_account(users["a"]["id"], "acct-A-empty")  # balance $0
    for acct in (funded, empty):
        resp = client_a.post("/goals", headers=HX, data={
            "goal_type": "payoff",
            "name": "No debt here",
            "account_id": acct,
        })
        assert resp.status_code == 200  # error toast, no card
    assert find_goal_id(users["a"]["id"], "No debt here") is None


def test_payment_advances_payoff_and_charge_grows_remaining(users):
    acct = _debt_account(users, 500)
    gid = create_goal(users["a"]["id"], acct, 500, baseline=-500,
                      goal_type="payoff")

    # Pay $200 toward the card (a transfer in, like the app posts).
    create_transfer(users["a"]["id"], users["a"]["account_id"], acct, 200, TODAY)
    g = _goal_view(users["a"]["id"], gid)
    assert g["type"] == "payoff"
    assert g["saved"] == 200.0        # paid off so far
    assert g["remaining"] == 300.0    # still owed
    assert g["percent"] == 40.0

    # Charge $100 more — remaining self-corrects to the real debt.
    create_transaction(users["a"]["id"], acct, 100, TODAY,
                       transaction_type="expense")
    g = _goal_view(users["a"]["id"], gid)
    assert g["remaining"] == 400.0
    assert g["complete"] is False


def test_payoff_complete_when_balance_reaches_zero(users):
    acct = _debt_account(users, 300)
    gid = create_goal(users["a"]["id"], acct, 300, baseline=-300,
                      goal_type="payoff")
    create_transfer(users["a"]["id"], users["a"]["account_id"], acct, 300, TODAY)
    g = _goal_view(users["a"]["id"], gid)
    assert g["complete"] is True
    assert g["percent"] == 100.0
    assert g["remaining"] == 0.0


def test_edit_payoff_changes_name_and_date_only(client_a, users):
    acct = _debt_account(users, 500)
    gid = create_goal(users["a"]["id"], acct, 500, baseline=-500,
                      goal_type="payoff")
    other_acct = users["a"]["account_id"]
    resp = client_a.post(f"/goals/{gid}/edit", headers=HX, data={
        "goal_type": "payoff",
        "name": "Renamed card goal",
        "target_date": "2027-06-01",
        # even if a tampered form posts these, the stored type locks them:
        "target_amount": "9999",
        "account_id": other_acct,
    })
    assert resp.status_code == 200
    name, target, account_id, _, goal_type, baseline, target_date = fetch_goal(gid)
    assert name == "Renamed card goal"
    assert target_date.isoformat() == "2027-06-01"
    assert float(target) == 500.0     # locked to the creation snapshot
    assert account_id == acct         # locked
    assert float(baseline) == -500.0  # untouched
    assert goal_type == "payoff"


def find_goal_id(user_id, name):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM goals WHERE user_id = %s AND name = %s",
                (user_id, name))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None
