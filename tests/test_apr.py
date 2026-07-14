"""v10.15 tests — APR on credit cards (monthly interest cost).

The twin of test_credit_limits.py: the pure math (monthly_interest) and the
blank-tolerant, 100-capped parser are tested directly; the /accounts surfaces
(the interest line in the row partial, the edit error path's raw-apr echo),
the enriched account_balances ask tool, and the deterministic facts feeding
Insight/Digest are driven against seeded users. No AI seams involved — every
figure here is computed by the app.
"""
import json
from datetime import date
from decimal import Decimal

from app.db import get_db_connection
from app.blueprints.accounts import _parse_apr, monthly_interest
import app.blueprints.ask as ask
from tests.conftest import create_account, create_transaction

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


# --- pure: monthly_interest ------------------------------------------------------

def test_interest_none_without_usable_apr():
    assert monthly_interest(-1000.0, None) is None
    assert monthly_interest(-1000.0, 0) is None
    assert monthly_interest(-1000.0, -5) is None
    assert monthly_interest(-1000.0, float("nan")) is None


def test_interest_none_without_debt():
    # A paid-off or overpaid card costs nothing — no line, no facts entry.
    assert monthly_interest(0.0, 24.99) is None
    assert monthly_interest(250.0, 24.99) is None


def test_interest_decimal_inputs_do_not_raise():
    # psycopg2 hands numeric back as Decimal — the helper must coerce.
    mi = monthly_interest(Decimal("-1200.00"), Decimal("24.99"))
    assert mi["debt"] == 1200.0
    assert mi["apr"] == 24.99
    assert mi["monthly"] == 24.99  # 1200 × 0.2499 / 12


def test_interest_rounds_to_cents():
    assert monthly_interest(-1000.0, 19.99)["monthly"] == 16.66


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


# --- ask tool: account_balances enrichment ---------------------------------------

def test_account_balances_enriched_with_apr(users):
    aid = create_account(users["a"]["id"], "AskAprCard", "Credit Card",
                         credit_limit=2000, apr=24.0)
    create_transaction(users["a"]["id"], aid, 1000, date.today())
    content, is_error = ask.dispatch(users["a"]["id"], "account_balances", {})
    assert is_error is False
    accounts = {a["account"]: a for a in json.loads(content)["accounts"]}
    card = accounts["AskAprCard"]
    assert card["apr"] == 24.0
    assert card["est_monthly_interest"] == 20.0  # 1000 × 0.24 / 12
    assert card["utilization_pct"] == 50.0  # the limit keys still ride along
    # The seeded bank account carries no interest keys.
    assert "apr" not in accounts["acct-A"]


def test_account_balances_apr_without_limit_still_enriched(users):
    aid = create_account(users["a"]["id"], "AskAprOnly", "Credit Card", apr=18.0)
    create_transaction(users["a"]["id"], aid, 600, date.today())
    content, _ = ask.dispatch(users["a"]["id"], "account_balances", {})
    accounts = {a["account"]: a for a in json.loads(content)["accounts"]}
    card = accounts["AskAprOnly"]
    assert card["est_monthly_interest"] == 9.0  # 600 × 0.18 / 12
    assert "utilization_pct" not in card  # independence from the limit


def test_account_balances_card_apr_no_debt_not_enriched(users):
    create_account(users["a"]["id"], "AskAprIdle", "Credit Card", apr=24.0)
    content, _ = ask.dispatch(users["a"]["id"], "account_balances", {})
    accounts = {a["account"]: a for a in json.loads(content)["accounts"]}
    assert "apr" not in accounts["AskAprIdle"]
    assert "est_monthly_interest" not in accounts["AskAprIdle"]


# --- /accounts rendering ---------------------------------------------------------

def test_card_with_apr_and_debt_shows_interest_line(client_a, users):
    aid = create_account(users["a"]["id"], "AprCard", "Credit Card", apr=24.99)
    create_transaction(users["a"]["id"], aid, 1200, date.today())
    html = client_a.get("/accounts").get_data(as_text=True)
    assert "~$24.99/mo interest at 24.99% APR" in html


def test_card_with_apr_no_debt_shows_no_interest_line(client_a, users):
    create_account(users["a"]["id"], "AprIdle", "Credit Card", apr=24.99)
    html = client_a.get("/accounts").get_data(as_text=True)
    assert "/mo interest" not in html


def test_card_without_apr_shows_no_interest_line(client_a, users):
    aid = create_account(users["a"]["id"], "NoAprCard", "Credit Card")
    create_transaction(users["a"]["id"], aid, 500, date.today())
    html = client_a.get("/accounts").get_data(as_text=True)
    assert "/mo interest" not in html


def test_bank_account_with_stored_apr_shows_no_interest_line(client_a, users):
    # An apr stored on a non-card (e.g. after a type flip) is ignored.
    aid = create_account(users["a"]["id"], "OddAprBank", "Bank Account", apr=24.99)
    create_transaction(users["a"]["id"], aid, 500, date.today())
    html = client_a.get("/accounts").get_data(as_text=True)
    assert "/mo interest" not in html


def test_interest_line_independent_of_limit(client_a, users):
    # APR without a credit limit: interest line renders, utilization bar doesn't.
    aid = create_account(users["a"]["id"], "AprNoLimit", "Credit Card", apr=12.0)
    create_transaction(users["a"]["id"], aid, 1000, date.today())
    html = client_a.get("/accounts").get_data(as_text=True)
    assert "~$10.00/mo interest at 12.0% APR" in html
    assert "credit-bar" not in html


def test_edit_save_returns_row_fragment_with_interest(client_a, users):
    aid = create_account(users["a"]["id"], "HtmxApr", "Credit Card")
    create_transaction(users["a"]["id"], aid, 600, date.today())
    resp = client_a.post(f"/accounts/{aid}/edit",
                         data={"name": "HtmxApr", "type": "Credit Card",
                               "credit_limit": "", "apr": "20"},
                         headers=HX)
    html = resp.get_data(as_text=True)
    assert "<html" not in html  # fragment, not a page
    assert "~$10.00/mo interest at 20.0% APR" in html  # 600 × 0.20 / 12
    assert float(_fetch_apr(aid)) == 20.0


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
