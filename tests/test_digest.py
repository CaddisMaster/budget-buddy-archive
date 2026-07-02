"""v10.8 tests — Weekly Email Digest.

No real Anthropic or Resend calls: the two network seams — app.ai._call_digest_model
and app.mailer._call_resend — are monkeypatched, so the fact-builder, the recipient
selection + idempotency guard, the send runner, the mailer, and the Profile opt-in
route all run while CI (no keys) stays offline and free.

The locked principle — "the app computes the numbers, the model only narrates" — is
exercised by compute_digest_facts() running directly against seeded data; the runner
is asserted on per-user DB state (not a global send count) so it's robust against any
other opted-in rows in the shared dev DB.
"""
from datetime import date, timedelta

import pytest

import app.ai as ai
import app.mailer as mailer
from app.ai import _Digest, ParseError
from app.mailer import MailError
from app.blueprints.digests import (
    compute_digest_facts, _upcoming_scheduled, _recipients, _most_recent_sunday,
    send_weekly_digests,
)
from app.db import get_db_connection
from tests.conftest import (
    create_account, create_category, create_transaction, create_schedule,
    create_transfer_schedule,
)

TODAY = date.today()


# --- opt-in helpers (no conftest helper for the new users columns) ----------

def _set_digest(user_id, email=None, weekly_digest=False, last_sent=None):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET email = %s, weekly_digest = %s, last_digest_sent_on = %s "
        "WHERE id = %s", (email, weekly_digest, last_sent, user_id))
    conn.commit()
    cur.close()
    conn.close()


def _last_sent(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT email, weekly_digest, last_digest_sent_on FROM users WHERE id = %s",
                (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row  # (email, weekly_digest, last_digest_sent_on)


class _DigestSeam:
    """Stand-in for _call_digest_model."""
    def __init__(self, boom=False):
        self.calls = 0
        self.boom = boom

    def __call__(self, *a, **k):
        self.calls += 1
        if self.boom:
            raise ParseError("model down")
        return _Digest(summary="Your week at a glance.", tips=["Save more"])


class _ResendSeam:
    """Stand-in for _call_resend that records recipients and can fail selectively."""
    def __init__(self, fail_for=None):
        self.sent_to = []
        self.fail_for = fail_for or set()

    def __call__(self, api_key, to, subject, html, reply_to):
        if to in self.fail_for:
            raise MailError("bounced")
        self.sent_to.append(to)
        return {"id": f"msg-{len(self.sent_to)}"}


# --- compute_digest_facts (deterministic, DB-backed) ------------------------

def test_compute_digest_facts_includes_month_and_upcoming(users):
    a = users["a"]["id"]
    acct = create_account(a, "dig-acct")
    cat = create_category(a, "dig-cat")
    create_schedule(a, acct, 500, "monthly", TODAY + timedelta(days=3),
                    transaction_type="expense", category_id=cat)   # in window
    create_schedule(a, acct, 900, "monthly", TODAY + timedelta(days=20),
                    transaction_type="expense", category_id=cat)   # out of window

    facts = compute_digest_facts(a, today=TODAY)
    assert facts["month_name"] and "income" in facts and "net" in facts
    amounts = [i["amount"] for i in facts["upcoming"]]
    assert 500.0 in amounts          # near schedule shows
    assert 900.0 not in amounts      # 20-days-out is excluded
    assert facts["upcoming_expense"] == 500.0


def test_compute_digest_facts_only_sees_own_rows(users):
    a, b = users["a"]["id"], users["b"]["id"]
    acct_b = create_account(b, "dig-b")
    cat_b = create_category(b, "dig-b-cat")
    create_schedule(b, acct_b, 777, "monthly", TODAY + timedelta(days=2),
                    transaction_type="expense", category_id=cat_b)
    facts = compute_digest_facts(a, today=TODAY)
    assert 777.0 not in [i["amount"] for i in facts["upcoming"]]  # B's never leaks


def test_upcoming_scheduled_window_and_transfers(users):
    a = users["a"]["id"]
    acct1 = create_account(a, "up-1")
    acct2 = create_account(a, "up-2")
    create_schedule(a, acct1, 100, "monthly", TODAY + timedelta(days=2),
                    transaction_type="income")               # in
    create_schedule(a, acct1, 200, "monthly", TODAY + timedelta(days=15))  # out (expense)
    create_transfer_schedule(a, acct1, acct2, 50, "monthly",
                             TODAY + timedelta(days=1))       # in (transfer)

    items = _upcoming_scheduled(a, TODAY, days=7)
    types = {i["type"] for i in items}
    amounts = {i["amount"] for i in items}
    assert "transfer" in types and "income" in types
    assert 100.0 in amounts and 50.0 in amounts
    assert 200.0 not in amounts                              # 15 days out excluded


# --- recipient selection + idempotency guard --------------------------------

def test_recipients_selects_only_opted_in_with_email_not_yet_sent(users):
    a, b = users["a"]["id"], users["b"]["id"]
    period_start = _most_recent_sunday(TODAY)
    _set_digest(a, email="a@test.dev", weekly_digest=True, last_sent=None)
    _set_digest(b, email="b@test.dev", weekly_digest=False)   # opted OUT
    conn = get_db_connection()
    cur = conn.cursor()
    ids = {r[0] for r in _recipients(cur, period_start)}
    cur.close()
    conn.close()
    assert a in ids           # opted in, has email, never sent
    assert b not in ids       # opted out


def test_recipients_excludes_opted_in_without_email(users):
    a = users["a"]["id"]
    period_start = _most_recent_sunday(TODAY)
    _set_digest(a, email=None, weekly_digest=True)            # on, but no address
    conn = get_db_connection()
    cur = conn.cursor()
    ids = {r[0] for r in _recipients(cur, period_start)}
    cur.close()
    conn.close()
    assert a not in ids


def test_recipients_excludes_already_sent_this_week(users):
    a = users["a"]["id"]
    period_start = _most_recent_sunday(TODAY)
    _set_digest(a, email="a@test.dev", weekly_digest=True, last_sent=TODAY)  # sent today
    conn = get_db_connection()
    cur = conn.cursor()
    ids = {r[0] for r in _recipients(cur, period_start)}
    cur.close()
    conn.close()
    assert a not in ids       # last_sent >= this week's Sunday → skipped


# --- send_weekly_digests (end-to-end, seams stubbed) ------------------------

@pytest.fixture
def _keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("RESEND_API_KEY", "test-resend-key")


def test_send_delivers_to_opted_in_and_sets_marker(users, monkeypatch, _keys):
    a, b = users["a"]["id"], users["b"]["id"]
    _set_digest(a, email="a@test.dev", weekly_digest=True, last_sent=None)
    _set_digest(b, email="b@test.dev", weekly_digest=False)   # opted out
    dseam, rseam = _DigestSeam(), _ResendSeam()
    monkeypatch.setattr(ai, "_call_digest_model", dseam)
    monkeypatch.setattr(mailer, "_call_resend", rseam)

    send_weekly_digests(today=TODAY)

    assert "a@test.dev" in rseam.sent_to            # opted-in user got the email
    assert "b@test.dev" not in rseam.sent_to        # opted-out user did not
    assert _last_sent(a)[2] == TODAY                # marker stamped
    assert _last_sent(b)[2] is None                 # untouched


def test_send_is_idempotent_within_the_week(users, monkeypatch, _keys):
    a = users["a"]["id"]
    _set_digest(a, email="a@test.dev", weekly_digest=True, last_sent=None)
    monkeypatch.setattr(ai, "_call_digest_model", _DigestSeam())
    monkeypatch.setattr(mailer, "_call_resend", _ResendSeam())
    send_weekly_digests(today=TODAY)                # sends, stamps last_sent=TODAY

    rseam2 = _ResendSeam()
    monkeypatch.setattr(mailer, "_call_resend", rseam2)
    send_weekly_digests(today=TODAY)                # same week → no re-send
    assert "a@test.dev" not in rseam2.sent_to


def test_send_one_failure_does_not_abort_batch(users, monkeypatch, _keys):
    a, b = users["a"]["id"], users["b"]["id"]
    _set_digest(a, email="a@test.dev", weekly_digest=True, last_sent=None)
    _set_digest(b, email="b@test.dev", weekly_digest=True, last_sent=None)
    monkeypatch.setattr(ai, "_call_digest_model", _DigestSeam())
    # A's send bounces; B's must still go out and get stamped.
    monkeypatch.setattr(mailer, "_call_resend", _ResendSeam(fail_for={"a@test.dev"}))

    send_weekly_digests(today=TODAY)

    assert _last_sent(a)[2] is None                 # failed → NOT stamped (retriable)
    assert _last_sent(b)[2] == TODAY                # unaffected by A's failure


# --- mailer.send_email seam -------------------------------------------------

def test_send_email_success(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "k")
    monkeypatch.setattr(mailer, "_call_resend",
                        lambda *a, **k: {"id": "abc123"})
    assert mailer.send_email("x@test.dev", "Hi", "<p>hi</p>") == "abc123"


def test_send_email_without_key_raises(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    with pytest.raises(MailError):
        mailer.send_email("x@test.dev", "Hi", "<p>hi</p>")


def test_send_email_no_id_is_failure(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "k")
    monkeypatch.setattr(mailer, "_call_resend", lambda *a, **k: {})  # no id
    with pytest.raises(MailError):
        mailer.send_email("x@test.dev", "Hi", "<p>hi</p>")


# --- Profile opt-in route ---------------------------------------------------

def test_profile_settings_saves_email_and_optin(client_a, users):
    a = users["a"]["id"]
    resp = client_a.post("/profile/settings",
                         data={"email": "me@test.dev", "weekly_digest": "on"},
                         follow_redirects=False)
    assert resp.status_code == 302
    email, weekly, _ = _last_sent(a)
    assert email == "me@test.dev" and weekly is True


def test_profile_settings_rejects_bad_email(client_a, users):
    a = users["a"]["id"]
    client_a.post("/profile/settings",
                  data={"email": "not-an-email", "weekly_digest": "on"})
    email, weekly, _ = _last_sent(a)
    assert email is None and weekly is False        # nothing saved


def test_profile_settings_optin_requires_email(client_a, users):
    a = users["a"]["id"]
    client_a.post("/profile/settings", data={"email": "", "weekly_digest": "on"})
    email, weekly, _ = _last_sent(a)
    assert weekly is False                          # can't opt in with no address


def test_profile_settings_only_touches_own_row(client_a, users):
    b = users["b"]["id"]
    _set_digest(b, email="b-orig@test.dev", weekly_digest=True)
    client_a.post("/profile/settings",
                  data={"email": "a@test.dev", "weekly_digest": "on"})
    # B's row is unchanged — the route is scoped to current_user.
    assert _last_sent(b)[0] == "b-orig@test.dev"


def test_profile_card_gated_on_mail_enabled(client_a, monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert b"Weekly email digest" not in client_a.get("/profile").data
    monkeypatch.setenv("RESEND_API_KEY", "k")
    assert b"Weekly email digest" in client_a.get("/profile").data
