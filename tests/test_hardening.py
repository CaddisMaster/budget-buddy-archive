"""v10.1.1 Hardening — security-bundle regressions.

Covers the pure CSV formula-injection sanitizer, the Flask after_request
security headers, the session cookie flags, and the constant-bcrypt login path
(no username enumeration / no error on a missing user). The IDOR write-side
guards live in test_isolation.py alongside the other isolation tests; the
rate-limit isn't unit-tested because the limiter is disabled under test.
"""
from app.blueprints.transactions import _csv_safe
from app.db import get_db_connection
from tests.conftest import USER_A, PASSWORD


# --- CSV formula-injection sanitizer (pure) ---------------------------------

def test_csv_safe_neutralizes_formula_prefixes():
    for trigger in ("=SUM(1)", "+1", "-1", "@cmd", "\ttab", "\rreturn"):
        out = _csv_safe(trigger)
        assert out == "'" + trigger


def test_csv_safe_leaves_normal_values_untouched():
    assert _csv_safe("groceries") == "groceries"
    assert _csv_safe("") == ""
    assert _csv_safe(42.5) == 42.5            # non-strings pass through
    assert _csv_safe(None) is None


def test_csv_export_neutralizes_formula(client_a, users):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO transactions (amount, description, account_id, "
        "transaction_type, transaction_date, user_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (5.0, "=SUM(A1:A2)", users["a"]["account_id"], "expense",
         "2026-01-01", users["a"]["id"]),
    )
    conn.commit()
    cur.close()
    conn.close()
    resp = client_a.get("/transactions/export")
    assert resp.status_code == 200
    assert "'=SUM(A1:A2)" in resp.data.decode()   # apostrophe-prefixed


# --- security headers (after_request) ---------------------------------------

def test_security_headers_present(anon_client):
    resp = anon_client.get("/login")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Content-Security-Policy"] == "frame-ancestors 'none'"
    assert resp.headers["Referrer-Policy"] == "no-referrer"


# --- session cookie flags ----------------------------------------------------

def test_login_sets_hardened_cookie_flags(anon_client, users):
    resp = anon_client.post(
        "/login",
        data={"username": USER_A, "password": PASSWORD},
        follow_redirects=False,
    )
    set_cookie = " ".join(resp.headers.get_all("Set-Cookie"))
    assert "session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=Lax" in set_cookie
    # Secure is gated on COOKIE_SECURE (unset under test), so it must NOT appear.
    assert "Secure" not in set_cookie


# --- login timing / username enumeration ------------------------------------

def test_login_with_unknown_user_is_rejected_cleanly(anon_client, users):
    # The always-run bcrypt dummy-hash path must not error on a missing user.
    resp = anon_client.post(
        "/login",
        data={"username": "__pytest__nope", "password": "whatever"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Invalid username or password" in resp.data
