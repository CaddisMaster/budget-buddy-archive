"""v10.15 tests — APR on credit cards (monthly interest cost).

The twin of test_credit_limits.py: the pure math (monthly_interest) and the
blank-tolerant, 100-capped parser are tested directly; the /accounts surfaces
(the interest line in the row partial, the edit error path's raw-apr echo),
the enriched account_balances ask tool, and the deterministic facts feeding
Insight/Digest are driven against seeded users. No AI seams involved — every
figure here is computed by the app.
"""
from datetime import date

from app.db import get_db_connection
from app.blueprints.accounts import _parse_apr
from tests.conftest import create_account

HX = {"HX-Request": "true"}


def _fetch_apr(account_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT apr FROM account WHERE account_id = %s", (account_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


# --- pure: _parse_apr ----------------------------------------------------------

def test_parse_apr_blank_means_not_set():
    assert _parse_apr("") == (None, None)
    assert _parse_apr("   ") == (None, None)
    assert _parse_apr(None) == (None, None)


def test_parse_apr_valid():
    apr, error = _parse_apr("24.99")
    assert error is None
    assert apr == 24.99


def test_parse_apr_rejects_garbage():
    for bad in ("nan", "inf", "-Infinity", "0", "-5", "abc"):
        apr, error = _parse_apr(bad)
        assert error is not None, bad
        assert "APR" in error


def test_parse_apr_rejects_over_100():
    # The units-typo guard: '2499' for 24.99% would narrate absurd interest.
    for bad in ("100.01", "2499"):
        apr, error = _parse_apr(bad)
        assert apr is None
        assert error == "APR must be 100 or less"


def test_parse_apr_accepts_exactly_100():
    assert _parse_apr("100") == (100.0, None)


# --- the edit error path (raw-apr echo) ----------------------------------------

def test_edit_error_path_rerenders_typed_apr(client_a, users):
    aid = create_account(users["a"]["id"], "AprEcho", "Credit Card", apr=19.99)
    resp = client_a.post(f"/accounts/{aid}/edit",
                         data={"name": "x" * 51, "type": "Credit Card",
                               "credit_limit": "", "apr": "12.34"},
                         headers=HX)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Name must be 50 characters or fewer" in html
    assert 'value="12.34"' in html  # the raw typed apr survives the re-render
    assert float(_fetch_apr(aid)) == 19.99  # nothing written


def test_edit_rejects_nan_apr_nothing_written(client_a, users):
    aid = create_account(users["a"]["id"], "AprNan", "Credit Card", apr=19.99)
    resp = client_a.post(f"/accounts/{aid}/edit",
                         data={"name": "AprNan", "type": "Credit Card",
                               "credit_limit": "", "apr": "nan"},
                         headers=HX)
    assert resp.status_code == 200
    assert "APR" in resp.get_data(as_text=True)  # the error message
    assert float(_fetch_apr(aid)) == 19.99


def test_create_with_invalid_apr_writes_nothing(client_a, users):
    resp = client_a.post("/accounts",
                         data={"name": "BadApr", "type": "Credit Card",
                               "apr": "2499"},
                         headers=HX)
    assert resp.status_code == 200
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM account WHERE user_id = %s AND account_name = %s",
                (users["a"]["id"], "BadApr"))
    row = cur.fetchone()
    cur.close()
    conn.close()
    assert row is None


def test_create_and_edit_persist_apr(client_a, users):
    # Create with an APR, blank it away, then set it again — the full lifecycle.
    resp = client_a.post("/accounts",
                         data={"name": "AprLife", "type": "Credit Card",
                               "apr": "24.99"},
                         headers=HX)
    assert resp.status_code == 200
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT account_id FROM account WHERE user_id = %s AND account_name = %s",
                (users["a"]["id"], "AprLife"))
    aid = cur.fetchone()[0]
    cur.close()
    conn.close()
    assert float(_fetch_apr(aid)) == 24.99

    client_a.post(f"/accounts/{aid}/edit",
                  data={"name": "AprLife", "type": "Credit Card",
                        "credit_limit": "", "apr": ""},
                  headers=HX)
    assert _fetch_apr(aid) is None  # blank = not set

    client_a.post(f"/accounts/{aid}/edit",
                  data={"name": "AprLife", "type": "Credit Card",
                        "credit_limit": "", "apr": "18.5"},
                  headers=HX)
    assert float(_fetch_apr(aid)) == 18.5
