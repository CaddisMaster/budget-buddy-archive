"""v10.3 tests — "Ask your finances" (tool-use, /ask + app.ai).

No real Anthropic API calls: the single network seam, app.ai._call_ask_model, is
monkeypatched to feed canned tool_use / text responses, so the real multi-turn
loop, the per-user tool dispatch, and the argument validation all run end-to-end
while CI (no key) stays offline. The dispatch + validators are also tested
directly against seeded users — the security boundary is the tool surface, so
that's where the isolation tests live.
"""
import json
from datetime import date
from types import SimpleNamespace

import app.ai as ai
from app.ai import answer_question, ParseError
import app.blueprints.ask as ask

HX = {"HX-Request": "true"}


# --- fake model blocks/responses (shape the SDK returns) --------------------

def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_block(name, tool_input, tid="t1"):
    return SimpleNamespace(type="tool_use", id=tid, name=name, input=tool_input)


def _resp(blocks, stop_reason):
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


# --- pure dispatch + argument validation ------------------------------------

def test_dispatch_unknown_tool_is_error():
    content, is_error = ask.dispatch(1, "no_such_tool", {})
    assert is_error is True
    assert "Unknown tool" in json.loads(content)["error"]


def test_dispatch_bad_month_is_error(users):
    content, is_error = ask.dispatch(
        users["a"]["id"], "spending_by_category", {"month": "May"})
    assert is_error is True
    assert "month" in json.loads(content)["error"]


def test_dispatch_list_categories_returns_own(users):
    content, is_error = ask.dispatch(users["a"]["id"], "list_categories", {})
    assert is_error is False
    assert json.loads(content)["categories"] == ["cat-A"]


def test_dispatch_unknown_category_lists_valid(users):
    content, is_error = ask.dispatch(users["a"]["id"], "total_for_category", {
        "category": "Nope", "start_date": "2026-01-01", "end_date": "2026-12-31"})
    assert is_error is True
    err = json.loads(content)["error"]
    assert "cat-A" in err          # the model is told the valid names to retry


def test_dispatch_search_limit_is_clamped(users):
    # An absurd limit must not error — it's clamped to MAX_LIMIT.
    content, is_error = ask.dispatch(users["a"]["id"], "search_transactions", {
        "text": "txn", "start_date": "2000-01-01", "end_date": "2100-01-01",
        "limit": 9999})
    assert is_error is False
    assert json.loads(content)["count"] >= 1


# --- per-user scoping (the security boundary) -------------------------------

def test_dispatch_search_only_sees_own_rows(users):
    a_content, _ = ask.dispatch(users["a"]["id"], "search_transactions", {
        "text": "txn", "start_date": "2000-01-01", "end_date": "2100-01-01"})
    matches = json.loads(a_content)["matches"]
    descs = [m["description"] for m in matches]
    assert "txn-A" in descs
    assert "txn-B" not in descs          # B's transaction never leaks to A


def test_dispatch_cannot_total_another_users_category(users):
    # A names B's category — resolution must reject it (A doesn't own it).
    content, is_error = ask.dispatch(users["a"]["id"], "total_for_category", {
        "category": "cat-B", "start_date": "2000-01-01", "end_date": "2100-01-01"})
    assert is_error is True
    assert "No category named 'cat-B'" in json.loads(content)["error"]


# --- the multi-turn loop ----------------------------------------------------

def test_answer_question_runs_tool_then_answers(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls = {"n": 0}
    seen = {}

    def fake_seam(messages, tool_specs, today, api_key):
        calls["n"] += 1
        if calls["n"] == 1:
            return _resp([_tool_block("list_categories", {})], "tool_use")
        return _resp([_text_block("You have one category: Groceries.")], "end_turn")

    def fake_dispatch(name, raw):
        seen["name"] = name
        return json.dumps({"categories": ["Groceries"]}), False

    monkeypatch.setattr(ai, "_call_ask_model", fake_seam)
    out = answer_question("what are my categories?", [], fake_dispatch, today=date(2026, 6, 26))
    assert out["answer"] == "You have one category: Groceries."
    assert out["tools_used"] == ["list_categories"]
    assert seen["name"] == "list_categories"


def test_answer_question_direct_answer_no_tool(monkeypatch):
    # Model can decline / answer without a tool — rendered as-is, no crash.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_call_ask_model",
                        lambda *a, **k: _resp([_text_block("I can't answer that.")], "end_turn"))
    out = answer_question("what's the weather?", [], lambda n, r: ("", False),
                          today=date(2026, 6, 26))
    assert out["answer"] == "I can't answer that."
    assert out["tools_used"] == []


def test_answer_question_turn_cap_raises(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    # Always asks for a tool → must give up after the cap instead of looping.
    monkeypatch.setattr(ai, "_call_ask_model",
                        lambda *a, **k: _resp([_tool_block("list_categories", {})], "tool_use"))
    try:
        answer_question("loop forever", [], lambda n, r: ("{}", False),
                        today=date(2026, 6, 26))
        assert False, "expected ParseError"
    except ParseError:
        pass


def test_answer_question_no_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    try:
        answer_question("x", [], lambda n, r: ("", False))
        assert False, "expected ParseError"
    except ParseError:
        pass


# --- route: auth + fragment + graceful fallback -----------------------------

def test_ask_requires_login(anon_client):
    resp = anon_client.post("/ask", data={"question": "x"})
    assert resp.status_code == 302


def test_ask_returns_answer_fragment(client_a, users, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls = {"n": 0}

    def fake_seam(messages, tool_specs, today, api_key):
        calls["n"] += 1
        if calls["n"] == 1:
            return _resp([_tool_block("list_categories", {})], "tool_use")
        return _resp([_text_block("Your only category is cat-A.")], "end_turn")

    monkeypatch.setattr(ai, "_call_ask_model", fake_seam)
    resp = client_a.post("/ask", data={"question": "my categories?"}, headers=HX)
    assert resp.status_code == 200
    assert b"<html" not in resp.data            # a fragment, not a full page
    assert b"Your only category is cat-A." in resp.data


def test_ask_api_error_falls_back(client_a, users, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def boom(*a, **k):
        raise ParseError("network down")
    monkeypatch.setattr(ai, "_call_ask_model", boom)
    resp = client_a.post("/ask", data={"question": "anything"}, headers=HX)
    assert resp.status_code == 200
    assert "showToast" in resp.headers.get("HX-Trigger", "")


def test_ask_empty_question_prompts(client_a, users):
    resp = client_a.post("/ask", data={"question": "  "}, headers=HX)
    assert resp.status_code == 200
    assert "showToast" in resp.headers.get("HX-Trigger", "")
