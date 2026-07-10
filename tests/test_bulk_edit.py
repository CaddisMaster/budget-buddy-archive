"""v10.11 tests — Bulk edit (multi-select on History + bulk action bar).

Two non-AI endpoints: POST /transactions/bulk/category and /transactions/bulk/delete.
The write-side guards mirror cleanup/apply (per-row ownership via WHERE user_id,
validate_category_account on the target category) plus one the single-row routes
don't have: transfer legs are excluded server-side (is_transfer = false), so a
crafted selection can never orphan half a transfer pair.
"""
from datetime import date

from app.db import get_db_connection
from conftest import create_transaction, create_category, create_transfer

HX = {"HX-Request": "true"}


# --- helpers ----------------------------------------------------------------

def _fetch_category_id(transaction_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT category_id FROM transactions WHERE id = %s", (transaction_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def _txn_exists(transaction_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM transactions WHERE id = %s", (transaction_id,))
    found = cur.fetchone() is not None
    cur.close()
    conn.close()
    return found


def _transfer_leg_ids(group_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM transactions WHERE transfer_group_id = %s ORDER BY id",
                (group_id,))
    ids = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return ids


# --- auth -------------------------------------------------------------------

def test_bulk_endpoints_require_login(anon_client):
    assert anon_client.post("/transactions/bulk/category").status_code == 302
    assert anon_client.post("/transactions/bulk/delete").status_code == 302


# --- bulk category ----------------------------------------------------------

def test_bulk_category_updates_checked_rows(users, client_a):
    a = users["a"]
    u1 = create_transaction(a["id"], a["account_id"], 5, date.today(), category_id=None)
    u2 = create_transaction(a["id"], a["account_id"], 6, date.today(), category_id=None)
    resp = client_a.post("/transactions/bulk/category", headers=HX, data={
        "row_id": [str(u1), str(u2)], "category_id": str(a["category_id"])})
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "<html" not in body                       # fragment, not a full page
    assert 'id="txn-rows"' in body                   # the refreshed tbody
    assert "2 transactions updated" in resp.headers.get("HX-Trigger", "")
    assert _fetch_category_id(u1) == a["category_id"]
    assert _fetch_category_id(u2) == a["category_id"]


def test_bulk_category_recategorizes_already_categorized_row(users, client_a):
    # The born-from pain: already-categorized rows CAN be moved (unlike
    # Auto-Categorize, which only surfaces uncategorized/high-disagreement rows).
    a = users["a"]
    new_cat = create_category(a["id"], "bulk-target-A")
    resp = client_a.post("/transactions/bulk/category", headers=HX, data={
        "row_id": str(a["transaction_id"]), "category_id": str(new_cat)})
    assert resp.status_code == 200
    assert _fetch_category_id(a["transaction_id"]) == new_cat


def test_bulk_category_rejects_other_users_category(users, client_a):
    a, b = users["a"], users["b"]
    u1 = create_transaction(a["id"], a["account_id"], 5, date.today(), category_id=None)
    resp = client_a.post("/transactions/bulk/category", headers=HX, data={
        "row_id": str(u1), "category_id": str(b["category_id"])})   # B's category
    assert resp.status_code == 200                   # degrades to a toast, no 500
    assert "Invalid category" in resp.headers.get("HX-Trigger", "")
    assert _fetch_category_id(u1) is None            # write-side IDOR blocked


def test_bulk_category_cannot_touch_other_users_transaction(users, client_a):
    a, b = users["a"], users["b"]
    b_txn = b["transaction_id"]                      # already cat-B
    client_a.post("/transactions/bulk/category", headers=HX, data={
        "row_id": str(b_txn), "category_id": str(a["category_id"])})
    assert _fetch_category_id(b_txn) == b["category_id"]  # B's row unchanged


def test_bulk_category_skips_transfer_leg(users, client_a):
    a = users["a"]
    gid = create_transfer(a["id"], a["account_id"], a["account_id"], 25, date.today())
    leg = _transfer_leg_ids(gid)[0]
    resp = client_a.post("/transactions/bulk/category", headers=HX, data={
        "row_id": str(leg), "category_id": str(a["category_id"])})
    assert resp.status_code == 200
    assert "Nothing applied" in resp.headers.get("HX-Trigger", "")
    assert _fetch_category_id(leg) is None           # transfer legs stay category-less


def test_bulk_category_skips_garbage_row_id(users, client_a):
    a = users["a"]
    u1 = create_transaction(a["id"], a["account_id"], 5, date.today(), category_id=None)
    resp = client_a.post("/transactions/bulk/category", headers=HX, data={
        "row_id": ["abc", str(u1)], "category_id": str(a["category_id"])})
    assert resp.status_code == 200                   # garbage skipped, not a 500
    assert _fetch_category_id(u1) == a["category_id"]


def test_bulk_category_invalid_category_writes_nothing(users, client_a):
    a = users["a"]
    u1 = create_transaction(a["id"], a["account_id"], 5, date.today(), category_id=None)
    for bad in ("abc", "", None):
        data = {"row_id": str(u1)}
        if bad is not None:
            data["category_id"] = bad
        resp = client_a.post("/transactions/bulk/category", headers=HX, data=data)
        assert resp.status_code == 200
        assert "Invalid category" in resp.headers.get("HX-Trigger", "")
    assert _fetch_category_id(u1) is None


def test_bulk_category_empty_selection(users, client_a):
    a = users["a"]
    resp = client_a.post("/transactions/bulk/category", headers=HX, data={
        "category_id": str(a["category_id"])})
    assert resp.status_code == 200
    assert "Nothing selected" in resp.headers.get("HX-Trigger", "")


def test_bulk_category_works_on_adjustment_row(users, client_a):
    a = users["a"]
    adj = create_transaction(a["id"], a["account_id"], 5, date.today(),
                             category_id=None, is_adjustment=True)
    client_a.post("/transactions/bulk/category", headers=HX, data={
        "row_id": str(adj), "category_id": str(a["category_id"])})
    assert _fetch_category_id(adj) == a["category_id"]  # adjustments are plain rows


def test_bulk_category_preserves_filters(users, client_a):
    # The endpoint URL carries filter_qs; the refreshed tbody must honour it.
    a = users["a"]
    in_month = create_transaction(a["id"], a["account_id"], 5, date(2026, 1, 15),
                                  category_id=None)
    out_month = create_transaction(a["id"], a["account_id"], 6, date(2026, 2, 15),
                                   category_id=None)
    resp = client_a.post("/transactions/bulk/category?month=2026-01", headers=HX, data={
        "row_id": str(in_month), "category_id": str(a["category_id"])})
    body = resp.get_data(as_text=True)
    assert f'id="txn-{in_month}"' in body
    assert f'id="txn-{out_month}"' not in body       # filtered out of the re-render


# --- bulk delete ------------------------------------------------------------

def test_bulk_delete_removes_checked_rows_only(users, client_a):
    a = users["a"]
    u1 = create_transaction(a["id"], a["account_id"], 5, date.today())
    u2 = create_transaction(a["id"], a["account_id"], 6, date.today())
    keep = create_transaction(a["id"], a["account_id"], 7, date.today())
    resp = client_a.post("/transactions/bulk/delete", headers=HX, data={
        "row_id": [str(u1), str(u2)]})
    assert resp.status_code == 200
    assert "2 transactions deleted" in resp.headers.get("HX-Trigger", "")
    assert not _txn_exists(u1)
    assert not _txn_exists(u2)
    assert _txn_exists(keep)


def test_bulk_delete_cannot_touch_other_users_transaction(users, client_a):
    b = users["b"]
    client_a.post("/transactions/bulk/delete", headers=HX, data={
        "row_id": str(b["transaction_id"])})
    assert _txn_exists(b["transaction_id"])          # B's row survives


def test_bulk_delete_skips_transfer_legs(users, client_a):
    a = users["a"]
    gid = create_transfer(a["id"], a["account_id"], a["account_id"], 25, date.today())
    legs = _transfer_leg_ids(gid)
    resp = client_a.post("/transactions/bulk/delete", headers=HX, data={
        "row_id": [str(i) for i in legs]})
    assert resp.status_code == 200
    assert "Nothing applied" in resp.headers.get("HX-Trigger", "")
    assert _transfer_leg_ids(gid) == legs            # the pair is intact


def test_bulk_delete_empty_selection(users, client_a):
    resp = client_a.post("/transactions/bulk/delete", headers=HX, data={})
    assert resp.status_code == 200
    assert "Nothing selected" in resp.headers.get("HX-Trigger", "")


# --- History page selection UI ----------------------------------------------

def test_history_renders_selection_ui(users, client_a):
    a = users["a"]
    resp = client_a.get("/transactions")
    body = resp.get_data(as_text=True)
    assert 'id="bulk-toggle"' in body                # the Bulk edit mode switch
    assert 'id="bulk-bar"' in body
    assert 'id="select-all"' in body
    assert 'id="bulk-category"' in body
    # The seeded normal transaction gets a row checkbox (CSS-hidden until the
    # toggle turns bulk-mode on, but always in the markup).
    assert f'name="row_id" value="{a["transaction_id"]}"' in body


def test_history_transfer_row_has_no_checkbox(users, client_a):
    a = users["a"]
    gid = create_transfer(a["id"], a["account_id"], a["account_id"], 25, date.today())
    body = client_a.get("/transactions").get_data(as_text=True)
    for leg in _transfer_leg_ids(gid):
        assert f'id="txn-{leg}"' in body             # the leg row renders...
        assert f'name="row_id" value="{leg}"' not in body   # ...without a checkbox
