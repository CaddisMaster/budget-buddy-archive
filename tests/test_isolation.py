"""Data-isolation tests — the security-critical ones.

Every route scopes its queries to current_user.id. These tests prove user A
cannot see, edit, or delete user B's data even when guessing B's row IDs.

As of v5.0, edit/delete of a missing or other-user row returns 404 (previously a
silent redirect with a misleading "success" flash), so these assert both the
404 status and that B's data is untouched.
"""
from app.db import get_db_connection
from tests.conftest import (
    create_budget,
    create_category,
    fetch_account,
    fetch_budget,
    fetch_budget_by_category,
    fetch_category,
    fetch_transaction,
)


def _count_transactions(user_id, category_id=None, account_id=None):
    conn = get_db_connection()
    cur = conn.cursor()
    sql = "SELECT COUNT(*) FROM transactions WHERE user_id = %s"
    params = [user_id]
    if category_id is not None:
        sql += " AND category_id = %s"
        params.append(category_id)
    if account_id is not None:
        sql += " AND account_id = %s"
        params.append(account_id)
    cur.execute(sql, params)
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    return n


def _count_schedules_for_user(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM schedules WHERE user_id = %s", (user_id,))
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    return n


def test_listing_only_shows_own_transactions(client_a, users):
    response = client_a.get("/transactions")
    assert response.status_code == 200
    assert b"txn-A" in response.data       # A's own transaction is listed
    assert b"txn-B" not in response.data   # B's transaction is not


# --- transactions -----------------------------------------------------------

def test_cannot_edit_another_users_transaction(client_a, users):
    b_txn = users["b"]["transaction_id"]
    # A submits an edit against B's transaction id with attacker-chosen values.
    response = client_a.post(
        f"/transactions/{b_txn}/edit",
        data={
            "amount": "9999.99",
            "description": "hacked-by-A",
            "transaction_date": "2026-01-01",
            "transaction_type": "expense",
        },
        follow_redirects=True,
    )
    assert response.status_code == 404
    amount, description, owner_id = fetch_transaction(b_txn)
    assert float(amount) == 42.50            # unchanged
    assert description == "txn-B"             # unchanged
    assert owner_id == users["b"]["id"]       # still B's


def test_cannot_delete_another_users_transaction(client_a, users):
    b_txn = users["b"]["transaction_id"]
    response = client_a.delete(f"/transactions/{b_txn}", follow_redirects=True)
    assert response.status_code == 404
    assert fetch_transaction(b_txn) is not None  # B's transaction survives


def test_can_delete_own_transaction(client_a, users):
    a_txn = users["a"]["transaction_id"]
    client_a.delete(f"/transactions/{a_txn}", follow_redirects=True)
    assert fetch_transaction(a_txn) is None


def test_missing_transaction_returns_404(client_a, users):
    response = client_a.get("/transactions/99999999/edit", follow_redirects=True)
    assert response.status_code == 404


# --- categories -------------------------------------------------------------

def test_cannot_edit_another_users_category(client_a, users):
    b_cat = users["b"]["category_id"]
    response = client_a.post(
        f"/categories/{b_cat}/edit",
        data={"name": "hacked", "description": "x"},
        follow_redirects=True,
    )
    assert response.status_code == 404
    name, _, owner_id = fetch_category(b_cat)
    assert name == "cat-B"               # unchanged
    assert owner_id == users["b"]["id"]   # still B's


def test_cannot_delete_another_users_category(client_a, users):
    b_cat = users["b"]["category_id"]
    response = client_a.delete(f"/categories/{b_cat}", follow_redirects=True)
    assert response.status_code == 404
    assert fetch_category(b_cat) is not None


# --- accounts ---------------------------------------------------------------

def test_cannot_edit_another_users_account(client_a, users):
    b_acct = users["b"]["account_id"]
    response = client_a.post(
        f"/accounts/{b_acct}/edit",
        data={"name": "hacked", "type": "Credit Card"},
        follow_redirects=True,
    )
    assert response.status_code == 404
    account_name, _, owner_id = fetch_account(b_acct)
    assert account_name == "acct-B"      # unchanged
    assert owner_id == users["b"]["id"]   # still B's


def test_cannot_delete_another_users_account(client_a, users):
    b_acct = users["b"]["account_id"]
    response = client_a.delete(f"/accounts/{b_acct}", follow_redirects=True)
    assert response.status_code == 404
    assert fetch_account(b_acct) is not None


# --- budgets ----------------------------------------------------------------

def test_cannot_set_budget_on_another_users_category(client_a, users):
    # A posts B's category_id to /budgets/set — the category ownership guard
    # must 404 and no budget row may be created against B's category.
    b_cat = users["b"]["category_id"]
    response = client_a.post(
        "/budgets/set",
        data={"category_id": b_cat, "amount": "9999"},
        follow_redirects=True,
    )
    assert response.status_code == 404
    assert fetch_budget_by_category(users["a"]["id"], b_cat) is None
    assert fetch_budget_by_category(users["b"]["id"], b_cat) is None


def test_cannot_clear_another_users_budget(client_a, users):
    b_budget = create_budget(users["b"]["id"], users["b"]["category_id"], 100.0)
    response = client_a.post(
        "/budgets/clear",
        data={"category_id": users["b"]["category_id"]},
        follow_redirects=True,
    )
    assert response.status_code == 404
    assert fetch_budget(b_budget) is not None   # still B's


# --- write-side IDOR (v10.1.1): can't attach a row to another user's FK -------

def test_cannot_add_transaction_with_another_users_category(client_a, users):
    # A submits a new transaction using its own account but B's category_id —
    # the ownership guard must reject it and write nothing.
    client_a.post(
        "/transactions/new",
        data={
            "amount": "10.00",
            "description": "idor-cat",
            "transaction_date": "2026-01-01",
            "transaction_type": "expense",
            "account_id": users["a"]["account_id"],
            "category_id": users["b"]["category_id"],
        },
        follow_redirects=True,
    )
    assert _count_transactions(users["a"]["id"], category_id=users["b"]["category_id"]) == 0


def test_cannot_add_transaction_with_another_users_account(client_a, users):
    # account_id is required; A supplies B's account_id — must be rejected.
    client_a.post(
        "/transactions/new",
        data={
            "amount": "10.00",
            "description": "idor-acct",
            "transaction_date": "2026-01-01",
            "transaction_type": "expense",
            "account_id": users["b"]["account_id"],
        },
        follow_redirects=True,
    )
    assert _count_transactions(users["a"]["id"], account_id=users["b"]["account_id"]) == 0


def test_cannot_edit_transaction_onto_another_users_category(client_a, users):
    # A edits its OWN transaction but tries to point it at B's category_id.
    a_txn = users["a"]["transaction_id"]
    client_a.post(
        f"/transactions/{a_txn}/edit",
        data={
            "amount": "10.00",
            "description": "idor-edit",
            "transaction_date": "2026-01-01",
            "transaction_type": "expense",
            "account_id": users["a"]["account_id"],
            "category_id": users["b"]["category_id"],
        },
        follow_redirects=True,
    )
    assert _count_transactions(users["a"]["id"], category_id=users["b"]["category_id"]) == 0


def test_cannot_add_schedule_with_another_users_account(client_a, users):
    # A creates a schedule pointing at B's account_id — must write nothing.
    before = _count_schedules_for_user(users["a"]["id"])
    client_a.post(
        "/scheduled",
        data={
            "amount": "10.00",
            "description": "idor-sched",
            "transaction_type": "expense",
            "frequency": "monthly",
            "next_due": "2099-01-01",
            "account_id": users["b"]["account_id"],
        },
        follow_redirects=True,
    )
    assert _count_schedules_for_user(users["a"]["id"]) == before


def test_cannot_add_schedule_with_another_users_category(client_a, users):
    before = _count_schedules_for_user(users["a"]["id"])
    client_a.post(
        "/scheduled",
        data={
            "amount": "10.00",
            "description": "idor-sched-cat",
            "transaction_type": "expense",
            "frequency": "monthly",
            "next_due": "2099-01-01",
            "account_id": users["a"]["account_id"],
            "category_id": users["b"]["category_id"],
        },
        follow_redirects=True,
    )
    assert _count_schedules_for_user(users["a"]["id"]) == before
