"""v10.10 tests — the Money agent (autonomous weekly investigation).

No real Anthropic or Resend calls: the agent's single network seam,
app.ai._call_agent_model, is monkeypatched with canned tool_use/text blocks
(the test_ask.py pattern), so the real loop, the submit_findings finish
protocol, the grounding guard, the per-user dispatch, the agent_runs upsert,
and the digest integration all run end-to-end while CI stays offline.

The free-hunt design has no deterministic pre-screen, so its guardrails ARE
the testable surface: the run must end via submit_findings, a submit without
any successful data-tool call is rejected, findings are capped at 3, and an
unevidenced finding is dropped by the pure normalizer.
"""
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import app.ai as ai
import app.mailer as mailer
from app.ai import (
    investigate_finances, _normalize_findings, ParseError,
    AGENT_MAX_FINDINGS, AGENT_TITLE_MAX,
)
import app.blueprints.ask as ask
from app.blueprints.agent import run_money_agent, load_agent_run
from app.blueprints.digests import send_weekly_digests
from app.helpers import most_recent_sunday
from app.db import get_db_connection
from tests.conftest import create_agent_run, fetch_agent_runs

HX = {"HX-Request": "true"}
TODAY = date.today()
WEEK = most_recent_sunday(TODAY)


# --- fake model blocks/responses (shape the SDK returns) --------------------

def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_block(name, tool_input, tid="t1"):
    return SimpleNamespace(type="tool_use", id=tid, name=name, input=tool_input)


def _submit_block(summary, findings, tid="t9"):
    return _tool_block("submit_findings",
                       {"summary": summary, "findings": findings}, tid=tid)


def _resp(blocks, stop_reason="tool_use"):
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


def _finding(n=1):
    return {"title": f"Finding {n}", "detail": f"Detail {n}",
            "evidence": f"Evidence {n}"}


_DATA_TOOL = _tool_block("recent_transactions", {
    "start_date": (TODAY - timedelta(days=7)).isoformat(),
    "end_date": TODAY.isoformat(),
})


class _AgentSeam:
    """Canned seam: a data-tool turn for the kickoff (and any nudge reply),
    then submit_findings once a tool_result has come back. Counts calls so
    cache-hit tests can assert the model was never re-consulted."""
    def __init__(self, summary="Nothing notable this week.", findings=None,
                 boom=False):
        self.summary = summary
        self.findings = findings if findings is not None else []
        self.boom = boom
        self.calls = 0

    def __call__(self, messages, tool_specs, today, api_key):
        self.calls += 1
        if self.boom:
            raise ParseError("agent down")
        if isinstance(messages[-1]["content"], str):
            return _resp([_DATA_TOOL])
        return _resp([_submit_block(self.summary, self.findings)])


def _ok_dispatch(name, raw):
    return json.dumps({"transactions": [], "count": 0}), False


# --- the loop (pure — fake seam + fake dispatch) -----------------------------

def test_loop_investigates_then_submits(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seen = {}

    def fake_dispatch(name, raw):
        seen["name"] = name
        return json.dumps({"transactions": [], "count": 0}), False

    monkeypatch.setattr(ai, "_call_agent_model",
                        _AgentSeam(summary="All quiet.", findings=[_finding()]))
    out = investigate_finances([], fake_dispatch, today=TODAY)
    assert out["summary"] == "All quiet."
    assert out["findings"] == [_finding()]
    assert out["tools_used"] == ["recent_transactions"]
    assert seen["name"] == "recent_transactions"


def test_loop_rejects_submit_without_any_tool_use(monkeypatch):
    # The grounding guard: a report from a model that never read any data is
    # noise by construction — the whole run fails (graceful ParseError).
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_call_agent_model",
                        lambda *a, **k: _resp([_submit_block("Looks fine.", [])]))
    with pytest.raises(ParseError):
        investigate_finances([], _ok_dispatch, today=TODAY)


def test_loop_failed_tool_calls_do_not_count_as_grounding(monkeypatch):
    # Every data-tool call errored → the model learned nothing → its submit is
    # still un-grounded and rejected.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seam = _AgentSeam(findings=[])

    def err_dispatch(name, raw):
        return json.dumps({"error": "bad args"}), True

    monkeypatch.setattr(ai, "_call_agent_model", seam)
    with pytest.raises(ParseError):
        investigate_finances([], err_dispatch, today=TODAY)


def test_loop_nudges_a_chatty_model_once(monkeypatch):
    # Turn 1: data tool. Turn 2: text instead of submit → nudge. Turn 3: submit.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls = {"n": 0}

    def fake_seam(messages, tool_specs, today, api_key):
        calls["n"] += 1
        if calls["n"] == 1:
            return _resp([_DATA_TOOL])
        if calls["n"] == 2:
            return _resp([_text_block("Here is what I found...")], "end_turn")
        assert "submit_findings" in messages[-1]["content"]  # the nudge text
        return _resp([_submit_block("Done.", [])])

    monkeypatch.setattr(ai, "_call_agent_model", fake_seam)
    out = investigate_finances([], _ok_dispatch, today=TODAY)
    assert out["summary"] == "Done."
    assert calls["n"] == 3


def test_loop_two_text_turns_is_an_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls = {"n": 0}

    def fake_seam(messages, tool_specs, today, api_key):
        calls["n"] += 1
        if calls["n"] == 1:
            return _resp([_DATA_TOOL])
        return _resp([_text_block("blah")], "end_turn")

    monkeypatch.setattr(ai, "_call_agent_model", fake_seam)
    with pytest.raises(ParseError):
        investigate_finances([], _ok_dispatch, today=TODAY)


def test_loop_turn_cap_raises(monkeypatch):
    # A model that investigates forever hits AGENT_MAX_TURNS, never a hang.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_call_agent_model",
                        lambda *a, **k: _resp([_DATA_TOOL]))
    with pytest.raises(ParseError):
        investigate_finances([], _ok_dispatch, today=TODAY)


def test_loop_without_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ParseError):
        investigate_finances([], _ok_dispatch, today=TODAY)


# --- _normalize_findings (pure) ----------------------------------------------

def test_normalize_caps_findings():
    raw = {"summary": "Busy week.",
           "findings": [_finding(i) for i in range(1, 6)]}
    out = _normalize_findings(raw)
    assert len(out["findings"]) == AGENT_MAX_FINDINGS


def test_normalize_drops_unevidenced_findings():
    raw = {"summary": "S.", "findings": [
        {"title": "No evidence", "detail": "D", "evidence": "  "},
        {"title": "", "detail": "D", "evidence": "E"},
        {"title": "Kept", "detail": "D", "evidence": "E"},
        "not-a-dict",
    ]}
    out = _normalize_findings(raw)
    assert [f["title"] for f in out["findings"]] == ["Kept"]


def test_normalize_empty_summary_raises():
    with pytest.raises(ParseError):
        _normalize_findings({"summary": "   ", "findings": []})


def test_normalize_trims_lengths():
    raw = {"summary": "S.", "findings": [
        {"title": "t" * 500, "detail": "d", "evidence": "e"}]}
    out = _normalize_findings(raw)
    assert len(out["findings"][0]["title"]) == AGENT_TITLE_MAX


# --- the recent_transactions ask tool (DB-backed dispatch) -------------------

def test_recent_transactions_returns_own_rows_with_flags(users):
    content, is_error = ask.dispatch(users["a"]["id"], "recent_transactions", {
        "start_date": "2000-01-01", "end_date": "2100-01-01"})
    assert is_error is False
    data = json.loads(content)
    descs = [t["description"] for t in data["transactions"]]
    assert "txn-A" in descs
    row = next(t for t in data["transactions"] if t["description"] == "txn-A")
    assert row["is_transfer"] is False and row["is_adjustment"] is False


def test_recent_transactions_never_leaks_other_users_rows(users):
    content, _ = ask.dispatch(users["a"]["id"], "recent_transactions", {
        "start_date": "2000-01-01", "end_date": "2100-01-01"})
    descs = [t["description"] for t in json.loads(content)["transactions"]]
    assert "txn-B" not in descs


def test_recent_transactions_validates_dates_and_clamps_limit(users):
    content, is_error = ask.dispatch(users["a"]["id"], "recent_transactions", {
        "start_date": "nope", "end_date": "2100-01-01"})
    assert is_error is True

    content, is_error = ask.dispatch(users["a"]["id"], "recent_transactions", {
        "start_date": "2000-01-01", "end_date": "2100-01-01", "limit": 99999})
    assert is_error is False
    assert json.loads(content)["count"] <= ask.MAX_LIMIT


# --- run_money_agent (DB-backed runner) --------------------------------------

def test_run_money_agent_caches_this_weeks_run(users, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    a = users["a"]["id"]
    monkeypatch.setattr(ai, "_call_agent_model",
                        _AgentSeam(summary="One thing.", findings=[_finding()]))

    out = run_money_agent(a, today=TODAY)

    assert out["period_start"] == WEEK
    runs = fetch_agent_runs(a)
    assert len(runs) == 1 and runs[0][0] == WEEK
    content = json.loads(runs[0][1])
    assert content["summary"] == "One thing."
    assert content["findings"] == [_finding()]
    assert content["tools_used"] == ["recent_transactions"]


def test_run_money_agent_rerun_overwrites_same_week(users, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    a = users["a"]["id"]
    monkeypatch.setattr(ai, "_call_agent_model", _AgentSeam(summary="First."))
    run_money_agent(a, today=TODAY)
    monkeypatch.setattr(ai, "_call_agent_model", _AgentSeam(summary="Second."))
    run_money_agent(a, today=TODAY)

    runs = fetch_agent_runs(a)
    assert len(runs) == 1                      # upsert, not a second row
    assert json.loads(runs[0][1])["summary"] == "Second."


def test_run_money_agent_is_user_isolated(users, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    a, b = users["a"]["id"], users["b"]["id"]
    monkeypatch.setattr(ai, "_call_agent_model", _AgentSeam())
    run_money_agent(a, today=TODAY)

    assert fetch_agent_runs(b) == []           # nothing written for B
    conn = get_db_connection()
    cur = conn.cursor()
    assert load_agent_run(cur, b) is None
    assert load_agent_run(cur, a)["summary"] == "Nothing notable this week."
    cur.close()
    conn.close()


def test_load_agent_run_specific_week_misses_stale(users):
    # The digest asks for THIS week's row — a last-week run must not satisfy it.
    a = users["a"]["id"]
    create_agent_run(a, WEEK - timedelta(days=7),
                     {"summary": "old", "findings": [], "tools_used": []})
    conn = get_db_connection()
    cur = conn.cursor()
    assert load_agent_run(cur, a, period_start=WEEK) is None
    assert load_agent_run(cur, a)["summary"] == "old"   # latest still loads
    cur.close()
    conn.close()


# --- POST /agent/run (route) -------------------------------------------------

def test_agent_run_requires_login(anon_client):
    resp = anon_client.post("/agent/run", headers=HX)
    assert resp.status_code == 302


def test_agent_run_returns_card_fragment_and_caches(client_a, users, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_call_agent_model",
                        _AgentSeam(summary="All good.", findings=[_finding()]))
    resp = client_a.post("/agent/run", headers=HX)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "<html" not in html                 # a fragment, not a page
    assert "Money agent" in html
    assert "Finding 1" in html and "Evidence 1" in html
    assert len(fetch_agent_runs(users["a"]["id"])) == 1


def test_agent_run_graceful_fallback_writes_nothing(client_a, users, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_call_agent_model", _AgentSeam(boom=True))
    resp = client_a.post("/agent/run", headers=HX)
    assert resp.status_code == 200             # never a broken page
    assert "showToast" in resp.headers.get("HX-Trigger", "")
    assert fetch_agent_runs(users["a"]["id"]) == []


def test_dashboard_shows_card_without_calling_model(client_a, users, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seam = _AgentSeam()
    monkeypatch.setattr(ai, "_call_agent_model", seam)
    create_agent_run(users["a"]["id"], WEEK,
                     {"summary": "Cached quiet week.", "findings": [],
                      "tools_used": ["recent_transactions"]})
    resp = client_a.get("/")
    html = resp.get_data(as_text=True)
    assert "Money agent" in html
    assert "Cached quiet week." in html
    assert seam.calls == 0                     # dashboard load is cache-only


def test_dashboard_hides_card_without_key(client_a, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = client_a.get("/")
    assert "Money agent" not in resp.get_data(as_text=True)


# --- digest integration -------------------------------------------------------

def _set_digest(user_id, email, weekly_digest=True):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET email = %s, weekly_digest = %s, "
        "last_digest_sent_on = NULL WHERE id = %s",
        (email, weekly_digest, user_id))
    conn.commit()
    cur.close()
    conn.close()


class _HtmlResend:
    """Recording resend stub that keeps the rendered html per recipient."""
    def __init__(self):
        self.html_by_to = {}

    def __call__(self, api_key, to, subject, html, reply_to):
        self.html_by_to[to] = html
        return {"id": f"msg-{len(self.html_by_to)}"}


@pytest.fixture
def _digest_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("RESEND_API_KEY", "test-resend-key")
    monkeypatch.setattr(
        ai, "_call_digest_model",
        lambda *a, **k: ai._Digest(summary="Week recap.", tips=[]))


def test_digest_includes_findings_section(users, monkeypatch, _digest_keys):
    a = users["a"]["id"]
    _set_digest(a, "agent-a@test.dev")
    monkeypatch.setattr(ai, "_call_agent_model",
                        _AgentSeam(summary="One flag.", findings=[_finding()]))
    rseam = _HtmlResend()
    monkeypatch.setattr(mailer, "_call_resend", rseam)

    send_weekly_digests(today=TODAY)

    html = rseam.html_by_to["agent-a@test.dev"]
    assert "This week's findings" in html
    assert "Finding 1" in html and "Evidence 1" in html
    assert len(fetch_agent_runs(a)) == 1       # the run was cached too


def test_digest_reuses_this_weeks_cached_run(users, monkeypatch, _digest_keys):
    a = users["a"]["id"]
    _set_digest(a, "agent-a@test.dev")
    create_agent_run(a, WEEK, {"summary": "Already ran this week.",
                               "findings": [], "tools_used": []})
    seam = _AgentSeam(summary="Should never be used.")
    monkeypatch.setattr(ai, "_call_agent_model", seam)
    rseam = _HtmlResend()
    monkeypatch.setattr(mailer, "_call_resend", rseam)

    send_weekly_digests(today=TODAY)

    assert seam.calls == 0                     # Sonnet not re-paid
    assert "Already ran this week." in rseam.html_by_to["agent-a@test.dev"]


def test_digest_still_sends_when_agent_fails(users, monkeypatch, _digest_keys):
    a = users["a"]["id"]
    _set_digest(a, "agent-a@test.dev")
    monkeypatch.setattr(ai, "_call_agent_model", _AgentSeam(boom=True))
    rseam = _HtmlResend()
    monkeypatch.setattr(mailer, "_call_resend", rseam)

    send_weekly_digests(today=TODAY)

    html = rseam.html_by_to["agent-a@test.dev"]   # the email went out
    assert "This week's findings" not in html      # just without the section
