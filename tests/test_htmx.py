"""v8.0 tests — HTMX inline-CRUD endpoints, the profile page, and the
admin-rejection / recurring coverage gaps folded into v8.

The inline endpoints return HTML *fragments* (a <tr>/<div>, or the history
<tbody>) rather than a full page, and signal success via an HX-Trigger header.
These assert the fragment shape, that the change landed in the DB, and that the
new routes keep the same per-user isolation as the pages they replaced.
"""
from datetime import date, timedelta

from app.db import get_db_connection
from tests.conftest import (
    USER_A,
    count_transactions_like,
    create_category,
    create_schedule,
    fetch_budget_by_category,
    fetch_category,
)

HX = {"HX-Request": "true"}


# --- categories -------------------------------------------------------------

def test_category_add_returns_row_fragment(client_a, users):
    resp = client_a.post("/categories",
                         data={"name": "HXCat", "description": "x"}, headers=HX)
    assert resp.status_code == 200
    assert b"<html" not in resp.data          # a fragment, not a full page
    assert b"HXCat" in resp.data
    assert b"<tr" in resp.data


def test_category_edit_fragment_then_save(client_a, users):
    cid = create_category(users["a"]["id"], "EditMe")
    # GET → edit-row fragment with an input
    resp = client_a.get(f"/categories/{cid}/edit", headers=HX)
    assert resp.status_code == 200
    assert b'name="name"' in resp.data
    assert b"<html" not in resp.data
    # POST → display-row fragment + DB updated
    resp = client_a.post(f"/categories/{cid}/edit",
                         data={"name": "Edited", "description": "y"}, headers=HX)
    assert resp.status_code == 200
    assert b"Edited" in resp.data
    assert fetch_category(cid)[0] == "Edited"


def test_category_row_fragment_for_cancel(client_a, users):
    cid = create_category(users["a"]["id"], "RowMe")
    resp = client_a.get(f"/categories/{cid}/row", headers=HX)
    assert resp.status_code == 200
    assert b"RowMe" in resp.data
    assert b"<html" not in resp.data


def test_category_delete_returns_empty_and_removes_row(client_a, users):
    cid = create_category(users["a"]["id"], "Gone")
    resp = client_a.delete(f"/categories/{cid}", headers=HX)
    assert resp.status_code == 200
    assert resp.data.strip() == b""           # empty body → htmx removes the row
    assert fetch_category(cid) is None


def test_category_edit_other_user_404(client_a, users):
    resp = client_a.get(f"/categories/{users['b']['category_id']}/edit", headers=HX)
    assert resp.status_code == 404


# --- accounts ---------------------------------------------------------------

def test_account_add_returns_row_fragment(client_a, users):
    resp = client_a.post("/accounts",
                         data={"name": "HXAcct", "type": "Bank Account"}, headers=HX)
    assert resp.status_code == 200
    assert b"<html" not in resp.data
    assert b"HXAcct" in resp.data


# --- budgets ----------------------------------------------------------------

def test_budget_set_returns_row_fragment(client_a, users):
    cid = create_category(users["a"]["id"], "BudHX")
    resp = client_a.post("/budgets/set",
                         data={"category_id": cid, "amount": "123"}, headers=HX)
    assert resp.status_code == 200
    assert b"<html" not in resp.data
    row = fetch_budget_by_category(users["a"]["id"], cid)
    assert row is not None and float(row[1]) == 123


# --- transactions -----------------------------------------------------------

def test_transaction_edit_fragment_and_delete_rerenders_tbody(client_a, users):
    tid = users["a"]["transaction_id"]
    resp = client_a.get(f"/transactions/{tid}/edit", headers=HX)
    assert resp.status_code == 200
    assert b'name="amount"' in resp.data
    assert b"<html" not in resp.data
    # delete re-renders the whole tbody (running balance recomputed)
    resp = client_a.delete(f"/transactions/{tid}", headers=HX)
    assert resp.status_code == 200
    assert b'id="txn-rows"' in resp.data


def test_transaction_rows_refresh_endpoint(client_a, users):
    resp = client_a.get("/transactions/rows", headers=HX)
    assert resp.status_code == 200
    assert b'id="txn-rows"' in resp.data
    assert b"<html" not in resp.data


# --- profile ----------------------------------------------------------------

def test_profile_shows_username(client_a, users):
    resp = client_a.get("/profile")
    assert resp.status_code == 200
    assert USER_A.encode() in resp.data


# --- admin rejection (coverage gap) -----------------------------------------

def test_non_admin_cannot_delete_user(client_a, users):
    resp = client_a.delete(f"/admin/users/{users['b']['id']}")
    assert resp.status_code == 403


def test_non_admin_cannot_toggle_admin(client_a, users):
    resp = client_a.post(f"/admin/users/toggle-admin/{users['b']['id']}")
    assert resp.status_code == 403


# --- schedule auto-generation (coverage gap) --------------------------------

def test_due_schedule_is_generated_on_history_load(client_a, users):
    """A due schedule materializes a plain transaction on GET /transactions; the
    schedule itself never appears in history."""
    uid = users["a"]["id"]
    yesterday = date.today() - timedelta(days=1)
    create_schedule(uid, users["a"]["account_id"], 10, "monthly", yesterday,
                    category_id=users["a"]["category_id"])
    # GET /transactions runs run_due_schedules(user)
    client_a.get("/transactions")
    assert count_transactions_like(uid, "seed-schedule") == 1  # exactly one occurrence
