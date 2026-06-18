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
    response = client_a.post(f"/goals/delete/{gid}", follow_redirects=True)
    assert response.status_code == 404
    assert fetch_goal(gid) is not None  # B's goal survives


def test_cannot_edit_another_users_goal(client_a, users):
    gid = create_goal(users["b"]["id"], users["b"]["account_id"], 500)
    response = client_a.post(
        f"/goals/edit/{gid}",
        data={"name": "hacked", "target_amount": "1",
              "account_id": users["b"]["account_id"]},
        follow_redirects=True,
    )
    assert response.status_code == 404
    name, target, _, owner = fetch_goal(gid)
    assert name == "seed-goal"            # unchanged
    assert float(target) == 500.0
    assert owner == users["b"]["id"]


def test_missing_goal_returns_404(client_a, users):
    response = client_a.get("/goals/edit/99999999", follow_redirects=True)
    assert response.status_code == 404
