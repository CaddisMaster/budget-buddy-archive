"""Data-isolation tests — the security-critical ones.

Every route scopes its queries to current_user.id. These tests prove user A
cannot see, edit, or delete user B's data even when guessing B's row IDs.
"""
from tests.conftest import fetch_transaction


def test_listing_only_shows_own_transactions(client_a, users):
    response = client_a.get("/transactions")
    assert response.status_code == 200
    assert b"txn-A" in response.data       # A's own transaction is listed
    assert b"txn-B" not in response.data   # B's transaction is not


def test_cannot_edit_another_users_transaction(client_a, users):
    b_txn = users["b"]["transaction_id"]
    # A submits an edit against B's transaction id with attacker-chosen values.
    client_a.post(
        f"/transactions/edit/{b_txn}",
        data={
            "amount": "9999.99",
            "description": "hacked-by-A",
            "transaction_date": "2026-01-01",
            "transaction_type": "expense",
        },
        follow_redirects=True,
    )
    amount, description, owner_id = fetch_transaction(b_txn)
    assert float(amount) == 42.50            # unchanged
    assert description == "txn-B"             # unchanged
    assert owner_id == users["b"]["id"]       # still B's


def test_cannot_delete_another_users_transaction(client_a, users):
    b_txn = users["b"]["transaction_id"]
    client_a.post(f"/transactions/delete/{b_txn}", follow_redirects=True)
    assert fetch_transaction(b_txn) is not None  # B's transaction survives


def test_can_delete_own_transaction(client_a, users):
    a_txn = users["a"]["transaction_id"]
    client_a.post(f"/transactions/delete/{a_txn}", follow_redirects=True)
    assert fetch_transaction(a_txn) is None
