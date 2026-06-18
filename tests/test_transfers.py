"""Account transfer tests.

A transfer is a linked pair of ledger rows (expense out of A, income into B)
sharing a transfer_group_id. These assert the pair stays consistent through
create/edit/delete, that balances move but analytics excludes the transfer, and
that one user can't touch another user's transfer group.
"""
from datetime import date

from tests.conftest import (
    account_balance,
    count_transfer_legs,
    create_account,
    create_transfer,
)

TODAY = date.today().isoformat()


# --- create -----------------------------------------------------------------

def test_create_transfer_moves_both_balances(client_a, users):
    from_acct = users["a"]["account_id"]
    to_acct = create_account(users["a"]["id"], "acct-A-dest")
    from_before = account_balance(from_acct)
    to_before = account_balance(to_acct)

    response = client_a.post(
        "/transfers",
        data={
            "from_account": from_acct,
            "to_account": to_acct,
            "amount": "150.00",
            "description": "rent savings",
            "transfer_date": TODAY,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert account_balance(from_acct) == from_before - 150.00
    assert account_balance(to_acct) == to_before + 150.00


def test_transfer_to_same_account_rejected(client_a, users):
    acct = users["a"]["account_id"]
    before = account_balance(acct)
    client_a.post(
        "/transfers",
        data={
            "from_account": acct,
            "to_account": acct,
            "amount": "50.00",
            "transfer_date": TODAY,
        },
        follow_redirects=True,
    )
    assert account_balance(acct) == before  # nothing recorded


# --- analytics exclusion vs balance inclusion -------------------------------

def test_transfer_excluded_from_analytics_but_kept_in_balance(client_a, users):
    # A's only real activity is the seeded 42.50 expense. A 500 transfer must
    # not inflate analytics income/expense, but must move account balances.
    from_acct = users["a"]["account_id"]
    to_acct = create_account(users["a"]["id"], "acct-A-savings")
    create_transfer(users["a"]["id"], from_acct, to_acct, 500.00, TODAY)

    response = client_a.get("/analytics")
    assert response.status_code == 200
    # Real expense total stays 42.50, not 542.50; real income stays 0.
    assert b">$42.50<" in response.data
    assert b"542.50" not in response.data
    assert b"500.00" not in response.data
    # But the money really moved between accounts.
    assert account_balance(to_acct) == 500.00


# --- edit (acts on the pair) ------------------------------------------------

def test_edit_transfer_updates_both_legs(client_a, users):
    from_acct = users["a"]["account_id"]
    to_acct = create_account(users["a"]["id"], "acct-A-2")
    gid = create_transfer(users["a"]["id"], from_acct, to_acct, 100.00, TODAY)

    response = client_a.post(
        f"/transfers/edit/{gid}",
        data={
            "from_account": from_acct,
            "to_account": to_acct,
            "amount": "250.00",
            "description": "bumped",
            "transfer_date": TODAY,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert count_transfer_legs(gid) == 2
    # Both legs now 250: destination gains the new amount, source loses it
    # (source also carries the seeded 42.50 expense → -292.50).
    assert account_balance(to_acct) == 250.00
    assert account_balance(from_acct) == -292.50


# --- delete (acts on the pair) ----------------------------------------------

def test_delete_transfer_removes_both_legs(client_a, users):
    from_acct = users["a"]["account_id"]
    to_acct = create_account(users["a"]["id"], "acct-A-3")
    gid = create_transfer(users["a"]["id"], from_acct, to_acct, 75.00, TODAY)
    assert count_transfer_legs(gid) == 2

    response = client_a.post(f"/transfers/delete/{gid}", follow_redirects=True)
    assert response.status_code == 200
    assert count_transfer_legs(gid) == 0


# --- isolation --------------------------------------------------------------

def test_cannot_delete_another_users_transfer(client_a, users):
    b_to = create_account(users["b"]["id"], "acct-B-2")
    gid = create_transfer(users["b"]["id"], users["b"]["account_id"], b_to, 60.00, TODAY)
    response = client_a.post(f"/transfers/delete/{gid}", follow_redirects=True)
    assert response.status_code == 404
    assert count_transfer_legs(gid) == 2  # B's transfer survives


def test_cannot_edit_another_users_transfer(client_a, users):
    b_to = create_account(users["b"]["id"], "acct-B-3")
    gid = create_transfer(users["b"]["id"], users["b"]["account_id"], b_to, 60.00, TODAY)
    response = client_a.post(
        f"/transfers/edit/{gid}",
        data={
            "from_account": users["b"]["account_id"],
            "to_account": b_to,
            "amount": "9999",
            "transfer_date": TODAY,
        },
        follow_redirects=True,
    )
    assert response.status_code == 404
    assert account_balance(b_to) == 60.00  # unchanged


def test_missing_transfer_group_returns_404(client_a, users):
    response = client_a.get("/transfers/edit/99999999", follow_redirects=True)
    assert response.status_code == 404


def test_history_renders_transfer_row(client_a, users):
    # Exercises the transfer-row branch in history.html (badge + group links).
    to_acct = create_account(users["a"]["id"], "acct-A-hist")
    gid = create_transfer(users["a"]["id"], users["a"]["account_id"], to_acct, 80.00, TODAY)
    response = client_a.get("/transactions")
    assert response.status_code == 200
    assert b"Transfer" in response.data
    assert f"/transfers/delete/{gid}".encode() in response.data
