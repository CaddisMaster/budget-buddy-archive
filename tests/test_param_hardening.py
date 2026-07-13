"""v10.10.1 hardening batch — tampered/malformed input must degrade gracefully
(fall back to a default or a clean validation error), never 500.

Covers the deferred items from the 2026-07-02 review + the 2026-07-07 re-review:
query-param parsing (?month / ?page on dashboard, history, CSV export), posted
ids that aren't ints (budgets set/clear, transaction edit, transfers, schedule
forms), the budget-review "inf" OverflowError, the forecasts/insights month=13
clamp, the bcrypt 72-byte password limit, the transaction-date validation, the
generic-error-message sweep (no raw psycopg2 text to the browser), and the
style.css cache-bust lockstep between base.html and login.html.

No real Anthropic API calls — the forecast/insight seams are monkeypatched with
the boom stub (the route's graceful ParseError fallback is the assertion).
"""
import re
from contextlib import contextmanager
from datetime import date, timedelta

import psycopg2
import pytest

import app.ai as ai
from app.ai import ParseError
from app.helpers import (
    GENERIC_ERROR, parse_int_param, parse_month_param, parse_page_param,
)
from tests.conftest import (
    PASSWORD, TEST_PREFIX, _create_user, _delete_user, _login,
    count_transfer_schedules, create_category, fetch_budget_by_category,
    fetch_transaction,
)

TODAY = date.today()


# --- pure parsers (helpers.py) -----------------------------------------------

def test_parse_month_param_accepts_valid_month():
    assert parse_month_param("2026-07") == "2026-07"


@pytest.mark.parametrize("raw", ["foo", "2024", "2026-13", "2026-00", "", None,
                                 "07-2026", "2026-7-1"])
def test_parse_month_param_rejects_garbage(raw):
    assert parse_month_param(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("3", 3), (7, 7), ("abc", 1), ("0", 1), ("-5", 1), ("", 1), (None, 1),
])
def test_parse_page_param_clamps_and_falls_back(raw, expected):
    assert parse_page_param(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("5", 5), (9, 9), ("abc", None), ("", None), (None, None), ("1.5", None),
])
def test_parse_int_param(raw, expected):
    assert parse_int_param(raw) == expected


# --- malformed query params → 200 fallback, not 500 --------------------------

@pytest.mark.parametrize("qs", ["?month=foo", "?month=2024", "?month=2026-13"])
def test_dashboard_bad_month_falls_back_to_all_time(client_a, qs):
    response = client_a.get(f"/{qs}")
    assert response.status_code == 200


@pytest.mark.parametrize("qs", ["?month=foo", "?page=abc", "?page=0", "?page=-1",
                                "?month=foo&page=abc"])
def test_history_bad_filters_fall_back(client_a, qs):
    response = client_a.get(f"/transactions{qs}")
    assert response.status_code == 200


def test_csv_export_bad_month_falls_back(client_a):
    response = client_a.get("/transactions/export?month=foo")
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/csv")


# --- posted garbage ids → clean error, not 500 --------------------------------

def test_budget_set_non_numeric_category_is_validation_error(client_a):
    response = client_a.post("/budgets/set",
                             data={"category_id": "abc", "amount": "100"})
    assert response.status_code == 302  # flash + redirect, the validation path


def test_budget_clear_non_numeric_category_404s(client_a):
    response = client_a.post("/budgets/clear", data={"category_id": "abc"})
    assert response.status_code == 404


def test_edit_transaction_non_numeric_ids_degrade(client_a, users):
    """A tampered category_id/account_id parses to None (unset) — the update
    still lands instead of 500ing in validate_category_account."""
    tid = users["a"]["transaction_id"]
    response = client_a.post(f"/transactions/{tid}/edit", data={
        "amount": "10", "description": "tampered-ids",
        "transaction_date": TODAY.isoformat(),
        "category_id": "abc", "account_id": str(users["a"]["account_id"]),
        "transaction_type": "expense",
    })
    assert response.status_code == 200
    assert fetch_transaction(tid)[1] == "tampered-ids"


def test_create_transfer_non_numeric_account_is_validation_error(client_a, users):
    response = client_a.post("/transfers", data={
        "from_account": "abc", "to_account": str(users["a"]["account_id"]),
        "amount": "5", "transfer_date": TODAY.isoformat(),
    })
    assert response.status_code == 302  # flash + redirect, not a 500


def test_create_transfer_schedule_non_numeric_account_is_validation_error(
        client_a, users):
    response = client_a.post("/transfers/recurring", data={
        "from_account": "abc", "to_account": str(users["a"]["account_id"]),
        "amount": "5", "frequency": "monthly",
        "next_due": (TODAY + timedelta(days=3)).isoformat(),
    })
    assert response.status_code == 302
    assert count_transfer_schedules(users["a"]["id"]) == 0


def test_create_schedule_non_numeric_account_is_validation_error(client_a):
    response = client_a.post("/scheduled", data={
        "amount": "5", "description": "bad-acct", "account_id": "abc",
        "transaction_type": "expense", "frequency": "monthly",
        "next_due": (TODAY + timedelta(days=3)).isoformat(),
    })
    assert response.status_code == 302  # "Account is required" flash + redirect


def test_budget_review_apply_inf_amount_is_skipped(client_a, users, monkeypatch):
    """int(round(float('inf'))) raised OverflowError — the row must skip like
    any other garbage amount."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")  # route 404s when AI off
    cat = create_category(users["a"]["id"], "rev-inf")
    response = client_a.post("/budgets/review/apply", data={
        "category_id": str(cat), f"amount_{cat}": "inf",
    })
    assert response.status_code == 200
    assert "Nothing applied" in response.headers.get("HX-Trigger", "")
    assert fetch_budget_by_category(users["a"]["id"], cat) is None


# --- tampered hidden year/month on the AI cards -------------------------------

def test_forecast_generate_month_13_clamps_to_today(client_a, monkeypatch):
    """calendar.monthrange(year, 13) raised IllegalMonthError → 500. The clamp
    falls back to the current month; the boom seam then exercises the route's
    graceful ParseError toast (and proves no real API call shape is needed)."""
    def _boom(*a, **k):
        raise ParseError("network down")
    monkeypatch.setattr(ai, "_call_forecast_model", _boom)
    response = client_a.post("/forecasts/generate",
                             data={"year": "2026", "month": "13"})
    assert response.status_code == 200


def test_insight_generate_month_13_clamps_to_today(client_a, monkeypatch):
    def _boom(*a, **k):
        raise ParseError("network down")
    monkeypatch.setattr(ai, "_call_insight_model", _boom)
    response = client_a.post("/insights/generate",
                             data={"year": "0", "month": "13"})
    assert response.status_code == 200


# --- transaction-date validation ----------------------------------------------

def test_new_transaction_invalid_date_is_validation_error(client_a, users):
    response = client_a.post("/transactions/new", data={
        "amount": "10", "description": "bad-date",
        "transaction_date": "not-a-date",
        "account_id": str(users["a"]["account_id"]),
        "transaction_type": "expense",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Date must be a valid date" in response.data
    from tests.conftest import count_transactions_like
    assert count_transactions_like(users["a"]["id"], "bad-date") == 0


def test_edit_transaction_invalid_date_is_validation_error(client_a, users):
    tid = users["a"]["transaction_id"]
    response = client_a.post(f"/transactions/{tid}/edit", data={
        "amount": "10", "description": "bad-date-edit",
        "transaction_date": "2026-13-45",
        "transaction_type": "expense",
    })
    assert response.status_code == 200
    assert b"Date must be a valid date" in response.data
    assert fetch_transaction(tid)[1] != "bad-date-edit"  # nothing written


# --- bcrypt 72-byte password limit ---------------------------------------------

def test_change_password_rejects_over_72_bytes(app, client_a, users):
    response = client_a.post("/change-password", data={
        "current_password": PASSWORD, "new_password": "p" * 73,
    }, follow_redirects=True)
    assert b"72 bytes" in response.data
    # The old password still works — nothing was silently truncated/updated.
    fresh = app.test_client()
    assert _login(fresh, users["a"]["username"]).status_code == 302


def test_change_password_accepts_72_bytes(client_a):
    response = client_a.post("/change-password", data={
        "current_password": PASSWORD, "new_password": "q" * 72,
    }, follow_redirects=True)
    assert b"Password updated" in response.data


ADMIN = TEST_PREFIX + "hardening_admin"
LONGPW_USER = TEST_PREFIX + "longpw_user"


@pytest.fixture
def admin_client(app):
    _delete_user(ADMIN)
    _delete_user(LONGPW_USER)
    _create_user(ADMIN, PASSWORD, is_admin=True)
    client = app.test_client()
    _login(client, ADMIN)
    yield client
    _delete_user(LONGPW_USER)
    _delete_user(ADMIN)


def test_admin_create_user_rejects_over_72_byte_password(admin_client):
    response = admin_client.post("/admin/create-user", data={
        "username": LONGPW_USER, "password": "p" * 73,
    }, follow_redirects=True)
    assert b"72 bytes" in response.data
    from tests.conftest import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM users WHERE username = %s", (LONGPW_USER,))
    created = cur.fetchone()
    cur.close(); conn.close()
    assert created is None


# --- generic error messages (no raw exception text to the browser) ------------

def test_db_error_shows_generic_message_not_exception_text(client_a, monkeypatch):
    """Representative site (categories create): a write failure must flash the
    generic message; the psycopg2/exception detail stays in the server log."""
    class _BoomCursor:
        def execute(self, sql, *a, **k):
            if sql.lstrip().upper().startswith("INSERT"):
                # A psycopg2 error, matching the narrowed write-path handlers
                # (except psycopg2.Error) — a generic Exception would now escape.
                raise psycopg2.OperationalError("SECRET-SQL-DETAIL")

        def fetchall(self):
            return []

        def fetchone(self):
            return None

    @contextmanager
    def _boom_cursor(commit=False):
        yield _BoomCursor()

    monkeypatch.setattr("app.blueprints.categories.db_cursor", _boom_cursor)
    response = client_a.post("/categories", data={"name": "boom-cat"},
                             follow_redirects=True)
    text = response.data.decode()
    assert "SECRET-SQL-DETAIL" not in text
    assert GENERIC_ERROR in text


# --- style.css cache-bust (automatic content hash) ------------------------------

def test_cache_bust_hash_rendered_on_login_and_app_pages(anon_client, client_a):
    """The ?v= is now a startup-computed hash of style.css content (css_v Jinja
    global) — both login.html (which doesn't extend base) and base.html must
    render it, and render the SAME value (the 2026-07-06 hotfix found login.html
    serving stale CSS with no version at all)."""
    pattern = re.compile(r"style\.css\?v=([0-9a-f]{8})")
    versions = {}
    for name, response in (("login", anon_client.get("/login")),
                           ("app", client_a.get("/"))):
        match = pattern.search(response.data.decode())
        assert match, f"{name} page is missing the style.css ?v= cache-bust hash"
        versions[name] = match.group(1)
    assert versions["login"] == versions["app"]
