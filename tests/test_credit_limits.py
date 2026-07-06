"""v10.10 tests — Credit limits (per-card utilization).

The pure math (credit_utilization) and the blank-tolerant parser are tested
directly; the /accounts surfaces (bar in the row partial, summary line, the
8-tuple edit error path — THE regression this feature hinges on), the enriched
account_balances ask tool, and the deterministic facts feeding Insight/Digest
(credit_card_utilization_facts + the compute_month_facts/compute_digest_facts
keys) are driven against seeded users. No AI seams involved — every figure here
is computed by the app.
"""
import json
from datetime import date
from decimal import Decimal

from app.db import get_db_connection
from app.blueprints.accounts import (
    credit_utilization, credit_card_utilization_facts, _parse_credit_limit,
)
import app.blueprints.ask as ask
from tests.conftest import create_account, create_transaction

HX = {"HX-Request": "true"}


def _fetch_credit_limit(account_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT credit_limit FROM account WHERE account_id = %s",
                (account_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


# --- pure: credit_utilization ------------------------------------------------

def test_utilization_none_without_usable_limit():
    assert credit_utilization(-100.0, None) is None
    assert credit_utilization(-100.0, 0) is None
    assert credit_utilization(-100.0, -500) is None


def test_utilization_decimal_inputs_do_not_raise():
    # psycopg2 hands numeric back as Decimal — the helper must coerce.
    cu = credit_utilization(Decimal("-250.00"), Decimal("1000.00"))
    assert cu["pct"] == 25.0
    assert cu["available"] == 750.0
    assert cu["debt"] == 250.0


def test_utilization_overpaid_card_reads_zero():
    cu = credit_utilization(50.0, 1000)
    assert cu["pct"] == 0.0
    assert cu["debt"] == 0.0
    assert cu["available"] == 1000.0
    assert cu["tier"] == "ok"


def test_utilization_tier_boundaries():
    assert credit_utilization(-299, 1000)["tier"] == "ok"      # 29.9%
    assert credit_utilization(-300, 1000)["tier"] == "warn"    # 30.0%
    assert credit_utilization(-799, 1000)["tier"] == "warn"    # 79.9%
    assert credit_utilization(-800, 1000)["tier"] == "danger"  # 80.0%


def test_utilization_over_limit_uncapped_pct_capped_bar():
    cu = credit_utilization(-1040, 1000)
    assert cu["pct"] == 104.0          # tells the truth
    assert cu["bar_pct"] == 100.0      # bar doesn't overflow
    assert cu["available"] == -40.0    # negative = over limit
    assert cu["tier"] == "danger"


def test_utilization_pct_rounds_to_one_decimal():
    assert credit_utilization(-333.333, 1000)["pct"] == 33.3


# --- pure: _parse_credit_limit ------------------------------------------------

def test_parse_credit_limit_blank_means_not_set():
    assert _parse_credit_limit("") == (None, None)
    assert _parse_credit_limit("   ") == (None, None)
    assert _parse_credit_limit(None) == (None, None)


def test_parse_credit_limit_valid():
    amount, error = _parse_credit_limit("5000")
    assert error is None
    assert amount == 5000.0


def test_parse_credit_limit_rejects_garbage():
    for bad in ("nan", "inf", "-Infinity", "0", "-5", "abc"):
        amount, error = _parse_credit_limit(bad)
        assert error is not None, bad
        assert "Credit limit" in error


# --- the 8-tuple edit error path (the regression) -----------------------------

def test_edit_error_path_rerenders_typed_limit(client_a, users):
    aid = create_account(users["a"]["id"], "Visa8", "Credit Card",
                         credit_limit=1000)
    resp = client_a.post(f"/accounts/{aid}/edit",
                         data={"name": "x" * 51, "type": "Credit Card",
                               "credit_limit": "1234"},
                         headers=HX)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Name must be 50 characters or fewer" in html
    assert 'value="1234"' in html  # the raw typed limit survives the re-render
    assert float(_fetch_credit_limit(aid)) == 1000.0  # nothing written


def test_edit_rejects_nan_limit_nothing_written(client_a, users):
    aid = create_account(users["a"]["id"], "VisaNan", "Credit Card",
                         credit_limit=1000)
    resp = client_a.post(f"/accounts/{aid}/edit",
                         data={"name": "VisaNan", "type": "Credit Card",
                               "credit_limit": "nan"},
                         headers=HX)
    assert resp.status_code == 200
    assert "Credit limit" in resp.get_data(as_text=True)  # the error message
    assert float(_fetch_credit_limit(aid)) == 1000.0


# --- /accounts rendering -------------------------------------------------------

def test_card_with_limit_shows_utilization(client_a, users):
    aid = create_account(users["a"]["id"], "Discover", "Credit Card",
                         credit_limit=2000)
    create_transaction(users["a"]["id"], aid, 500, date.today())
    html = client_a.get("/accounts").get_data(as_text=True)
    assert "credit-bar" in html
    assert "25.0% of $2000" in html
    assert "$1500.00 available" in html


def test_bank_account_with_stored_limit_shows_no_bar(client_a, users):
    # A limit stored on a non-card (e.g. after a type flip) is ignored.
    create_account(users["a"]["id"], "OddBank", "Bank Account",
                   credit_limit=2000)
    html = client_a.get("/accounts").get_data(as_text=True)
    assert "credit-bar" not in html


def test_card_without_limit_shows_no_bar(client_a, users):
    create_account(users["a"]["id"], "NoLimitCard", "Credit Card")
    html = client_a.get("/accounts").get_data(as_text=True)
    assert "credit-bar" not in html
    assert "Available credit across cards" not in html


def test_summary_line_sums_across_cards(client_a, users):
    a1 = create_account(users["a"]["id"], "Card1", "Credit Card",
                        credit_limit=1000)
    create_account(users["a"]["id"], "Card2", "Credit Card", credit_limit=3000)
    create_transaction(users["a"]["id"], a1, 400, date.today())
    html = client_a.get("/accounts").get_data(as_text=True)
    # (1000 - 400) + 3000 = 3600 available of 4000 in limits
    assert "Available credit across cards" in html
    assert "$3600.00" in html
    assert "$4000 in limits" in html


def test_edit_save_returns_row_fragment_with_bar(client_a, users):
    aid = create_account(users["a"]["id"], "HtmxCard", "Credit Card")
    create_transaction(users["a"]["id"], aid, 900, date.today())
    resp = client_a.post(f"/accounts/{aid}/edit",
                         data={"name": "HtmxCard", "type": "Credit Card",
                               "credit_limit": "1000"},
                         headers=HX)
    html = resp.get_data(as_text=True)
    assert "<html" not in html  # fragment, not a page
    assert "credit-bar" in html
    assert "danger" in html  # 90% utilization tints red
    assert float(_fetch_credit_limit(aid)) == 1000.0


def test_create_with_invalid_limit_writes_nothing(client_a, users):
    resp = client_a.post("/accounts",
                         data={"name": "BadLimit", "type": "Credit Card",
                               "credit_limit": "inf"},
                         headers=HX)
    assert resp.status_code == 200
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM account WHERE user_id = %s AND account_name = %s",
                (users["a"]["id"], "BadLimit"))
    row = cur.fetchone()
    cur.close()
    conn.close()
    assert row is None


# --- ask tool: account_balances enrichment ------------------------------------

def test_account_balances_enriched_for_card_with_limit(users):
    aid = create_account(users["a"]["id"], "AskCard", "Credit Card",
                         credit_limit=1000)
    create_transaction(users["a"]["id"], aid, 720, date.today())
    content, is_error = ask.dispatch(users["a"]["id"], "account_balances", {})
    assert is_error is False
    accounts = {a["account"]: a for a in json.loads(content)["accounts"]}
    card = accounts["AskCard"]
    assert card["credit_limit"] == 1000.0
    assert card["available_credit"] == 280.0
    assert card["utilization_pct"] == 72.0
    # The seeded bank account carries no utilization keys.
    assert "credit_limit" not in accounts["acct-A"]


def test_account_balances_card_without_limit_not_enriched(users):
    create_account(users["a"]["id"], "PlainCard", "Credit Card")
    content, _ = ask.dispatch(users["a"]["id"], "account_balances", {})
    accounts = {a["account"]: a for a in json.loads(content)["accounts"]}
    assert "credit_limit" not in accounts["PlainCard"]


# --- facts for Insight / Digest ------------------------------------------------

def test_utilization_facts_figures(users):
    aid = create_account(users["a"]["id"], "FactCard", "Credit Card",
                         credit_limit=2000)
    create_transaction(users["a"]["id"], aid, 500, date.today())
    create_account(users["a"]["id"], "FactBank", "Bank Account")

    facts = credit_card_utilization_facts(users["a"]["id"])
    assert facts == [{"name": "FactCard", "limit": 2000.0, "debt": 500.0,
                      "available": 1500.0, "utilization_pct": 25.0}]


def test_utilization_facts_empty_without_limits(users):
    create_account(users["a"]["id"], "EmptyCard", "Credit Card")
    assert credit_card_utilization_facts(users["a"]["id"]) == []


def test_utilization_facts_user_isolation(users):
    create_account(users["a"]["id"], "IsoCardA", "Credit Card",
                   credit_limit=1000)
    facts_b = credit_card_utilization_facts(users["b"]["id"])
    assert all(f["name"] != "IsoCardA" for f in facts_b)
    assert facts_b == []


def test_month_facts_carry_credit_cards(users):
    from app.blueprints.insights import compute_month_facts
    aid = create_account(users["a"]["id"], "InsightCard", "Credit Card",
                         credit_limit=1000)
    create_transaction(users["a"]["id"], aid, 300, date.today())
    today = date.today()
    facts = compute_month_facts(users["a"]["id"], today.year, today.month)
    assert facts["credit_cards"] == [
        {"name": "InsightCard", "limit": 1000.0, "debt": 300.0,
         "available": 700.0, "utilization_pct": 30.0}]


def test_digest_facts_pass_credit_cards_through(users):
    from app.blueprints.digests import compute_digest_facts
    create_account(users["a"]["id"], "DigestCard", "Credit Card",
                   credit_limit=5000)
    facts = compute_digest_facts(users["a"]["id"])
    assert facts["credit_cards"] == [
        {"name": "DigestCard", "limit": 5000.0, "debt": 0.0,
         "available": 5000.0, "utilization_pct": 0.0}]
