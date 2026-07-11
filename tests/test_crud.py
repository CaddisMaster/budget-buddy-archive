"""CRUD happy-path tests for categories, accounts, and budgets.

Phase 20 only smoke-tested that these pages render. These drive the actual
create / edit / delete flows through the app (as the logged-in owner) and verify
the change landed in the DB. The owner path is the complement to the 404 /
isolation tests in test_isolation.py.
"""
from app.db import get_db_connection
from tests.conftest import (
    create_category,
    fetch_account,
    fetch_budget,
    fetch_category,
    find_category_id,
)


# --- categories -------------------------------------------------------------

def test_create_category(client_a, users):
    client_a.post("/categories", data={"name": "Groceries",
                                        "description": "food"},
                  follow_redirects=True)
    cid = find_category_id(users["a"]["id"], "Groceries")
    assert cid is not None
    name, description, owner_id = fetch_category(cid)
    assert name == "Groceries"
    assert description == "food"
    assert owner_id == users["a"]["id"]


def test_edit_category(client_a, users):
    cid = create_category(users["a"]["id"], "Original")
    client_a.post(f"/categories/{cid}/edit",
                  data={"name": "Renamed", "description": "now edited"},
                  follow_redirects=True)
    name, description, _ = fetch_category(cid)
    assert name == "Renamed"
    assert description == "now edited"


def test_delete_category(client_a, users):
    cid = create_category(users["a"]["id"], "Disposable")
    client_a.delete(f"/categories/{cid}", follow_redirects=True)
    assert fetch_category(cid) is None


# --- accounts ---------------------------------------------------------------

def _find_account_id(user_id, name):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT account_id FROM account WHERE user_id = %s AND account_name = %s",
                (user_id, name))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def test_create_account(client_a, users):
    client_a.post("/accounts", data={"name": "Checking", "type": "Bank Account"},
                  follow_redirects=True)
    aid = _find_account_id(users["a"]["id"], "Checking")
    assert aid is not None
    account_name, acct_type, owner_id = fetch_account(aid)
    assert account_name == "Checking"
    assert acct_type == "Bank Account"
    assert owner_id == users["a"]["id"]


def test_edit_account(client_a, users):
    client_a.post("/accounts", data={"name": "OldName", "type": "Debit Card"},
                  follow_redirects=True)
    aid = _find_account_id(users["a"]["id"], "OldName")
    client_a.post(f"/accounts/{aid}/edit",
                  data={"name": "NewName", "type": "Credit Card"},
                  follow_redirects=True)
    account_name, acct_type, _ = fetch_account(aid)
    assert account_name == "NewName"
    assert acct_type == "Credit Card"


def _fetch_credit_limit(account_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT credit_limit FROM account WHERE account_id = %s",
                (account_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def test_account_credit_limit_persists(client_a, users):
    # v10.10: set on create.
    client_a.post("/accounts", data={"name": "Limity", "type": "Credit Card",
                                     "credit_limit": "5000"},
                  follow_redirects=True)
    aid = _find_account_id(users["a"]["id"], "Limity")
    assert float(_fetch_credit_limit(aid)) == 5000.00
    # Blanked on edit → NULL (not set).
    client_a.post(f"/accounts/{aid}/edit",
                  data={"name": "Limity", "type": "Credit Card",
                        "credit_limit": ""},
                  follow_redirects=True)
    assert _fetch_credit_limit(aid) is None
    # Set again on edit → updated.
    client_a.post(f"/accounts/{aid}/edit",
                  data={"name": "Limity", "type": "Credit Card",
                        "credit_limit": "7500.50"},
                  follow_redirects=True)
    assert float(_fetch_credit_limit(aid)) == 7500.50


def test_delete_account(client_a, users):
    client_a.post("/accounts", data={"name": "ToDelete", "type": "Bank Account"},
                  follow_redirects=True)
    aid = _find_account_id(users["a"]["id"], "ToDelete")
    client_a.delete(f"/accounts/{aid}", follow_redirects=True)
    assert fetch_account(aid) is None


# --- budgets ----------------------------------------------------------------

def _find_budget_id(user_id, category_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM budgets WHERE user_id = %s AND category_id = %s",
                (user_id, category_id))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def test_set_budget(client_a, users):
    cid = create_category(users["a"]["id"], "BudgetCat")
    client_a.post("/budgets/set", data={
        "category_id": cid, "amount": "250.00",
    }, follow_redirects=True)
    bid = _find_budget_id(users["a"]["id"], cid)
    assert bid is not None
    category_id, amount, owner_id = fetch_budget(bid)
    assert category_id == cid
    assert float(amount) == 250.00
    assert owner_id == users["a"]["id"]


def test_set_budget_upserts_in_place(client_a, users):
    """A second set on the same category updates the existing row, not a new one."""
    cid = create_category(users["a"]["id"], "BudgetEditCat")
    client_a.post("/budgets/set", data={"category_id": cid, "amount": "100.00"},
                  follow_redirects=True)
    bid = _find_budget_id(users["a"]["id"], cid)
    client_a.post("/budgets/set", data={"category_id": cid, "amount": "175.00"},
                  follow_redirects=True)
    # Same row, new amount — no duplicate row created.
    assert _find_budget_id(users["a"]["id"], cid) == bid
    _, amount, _ = fetch_budget(bid)
    assert float(amount) == 175.00


def test_clear_budget(client_a, users):
    cid = create_category(users["a"]["id"], "BudgetDelCat")
    client_a.post("/budgets/set", data={"category_id": cid, "amount": "50.00"},
                  follow_redirects=True)
    bid = _find_budget_id(users["a"]["id"], cid)
    client_a.post("/budgets/clear", data={"category_id": cid}, follow_redirects=True)
    assert fetch_budget(bid) is None
