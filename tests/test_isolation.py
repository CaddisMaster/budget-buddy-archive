"""Data-isolation tests — the security-critical ones.

Every route scopes its queries to current_user.id. These tests prove user A
cannot see, edit, or delete user B's data even when guessing B's row IDs.

As of v5.0, edit/delete of a missing or other-user row returns 404 (previously a
silent redirect with a misleading "success" flash), so these assert both the
404 status and that B's data is untouched.
"""
from tests.conftest import (
    create_budget,
    create_category,
    fetch_account,
    fetch_budget,
    fetch_category,
    fetch_transaction,
)


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
        f"/transactions/edit/{b_txn}",
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
    response = client_a.post(f"/transactions/delete/{b_txn}", follow_redirects=True)
    assert response.status_code == 404
    assert fetch_transaction(b_txn) is not None  # B's transaction survives


def test_can_delete_own_transaction(client_a, users):
    a_txn = users["a"]["transaction_id"]
    client_a.post(f"/transactions/delete/{a_txn}", follow_redirects=True)
    assert fetch_transaction(a_txn) is None


def test_missing_transaction_returns_404(client_a, users):
    response = client_a.get("/transactions/edit/99999999", follow_redirects=True)
    assert response.status_code == 404


# --- categories -------------------------------------------------------------

def test_cannot_edit_another_users_category(client_a, users):
    b_cat = users["b"]["category_id"]
    response = client_a.post(
        f"/categories/edit/{b_cat}",
        data={"name": "hacked", "description": "x"},
        follow_redirects=True,
    )
    assert response.status_code == 404
    name, _, owner_id = fetch_category(b_cat)
    assert name == "cat-B"               # unchanged
    assert owner_id == users["b"]["id"]   # still B's


def test_cannot_delete_another_users_category(client_a, users):
    b_cat = users["b"]["category_id"]
    response = client_a.post(f"/categories/delete/{b_cat}", follow_redirects=True)
    assert response.status_code == 404
    assert fetch_category(b_cat) is not None


# --- accounts ---------------------------------------------------------------

def test_cannot_edit_another_users_account(client_a, users):
    b_acct = users["b"]["account_id"]
    response = client_a.post(
        f"/accounts/edit/{b_acct}",
        data={"name": "hacked", "type": "Credit Card"},
        follow_redirects=True,
    )
    assert response.status_code == 404
    account_name, _, owner_id = fetch_account(b_acct)
    assert account_name == "acct-B"      # unchanged
    assert owner_id == users["b"]["id"]   # still B's


def test_cannot_delete_another_users_account(client_a, users):
    b_acct = users["b"]["account_id"]
    response = client_a.post(f"/accounts/delete/{b_acct}", follow_redirects=True)
    assert response.status_code == 404
    assert fetch_account(b_acct) is not None


# --- budgets ----------------------------------------------------------------

def test_cannot_edit_another_users_budget(client_a, users):
    b_budget = create_budget(users["b"]["id"], users["b"]["category_id"],
                             100.0, "2026-06-01", "2026-06-30")
    response = client_a.post(
        f"/budgets/edit/{b_budget}",
        data={"category_id": users["b"]["category_id"], "amount": "9999",
              "period_start": "2026-06-01", "period_end": "2026-06-30"},
        follow_redirects=True,
    )
    assert response.status_code == 404
    _, amount, owner_id = fetch_budget(b_budget)
    assert float(amount) == 100.0        # unchanged
    assert owner_id == users["b"]["id"]   # still B's


def test_cannot_delete_another_users_budget(client_a, users):
    b_budget = create_budget(users["b"]["id"], users["b"]["category_id"],
                             100.0, "2026-06-01", "2026-06-30")
    response = client_a.post(f"/budgets/delete/{b_budget}", follow_redirects=True)
    assert response.status_code == 404
    assert fetch_budget(b_budget) is not None
