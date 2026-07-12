"""v9.0 tests — natural-language quick-add (/transactions/parse + app.ai).

No real Anthropic API calls: the single network seam, app.ai._call_model, is
monkeypatched to return a canned _ParsedTransaction, so the real validation /
ownership-resolution logic and the route both run end-to-end while CI (which has
no ANTHROPIC_API_KEY) stays offline and free. The pure helpers (_normalize,
_match_id) are also tested directly.
"""
from collections import namedtuple
from datetime import date

import app.ai as ai
from app.ai import _ParsedTransaction, _match_id, _normalize, ParseError

HX = {"HX-Request": "true"}

# ai.py reads owned rows by attribute (v10.12) — mirror the feeder shape.
Row = namedtuple("Row", "id name")


def _parsed(**over):
    base = dict(transaction_type="expense", amount=42.5, description="groceries",
                category=None, account=None, transaction_date="2026-06-20")
    base.update(over)
    return _ParsedTransaction(**base)


# --- pure helpers -----------------------------------------------------------

def test_match_id_is_case_insensitive():
    rows = [Row(1, "Groceries"), Row(2, "Rent")]
    assert _match_id("groceries", rows) == 1
    assert _match_id("RENT", rows) == 2


def test_match_id_unmatched_or_none():
    rows = [Row(1, "Groceries")]
    assert _match_id("Utilities", rows) is None
    assert _match_id(None, rows) is None
    assert _match_id("", rows) is None


def test_normalize_resolves_names_to_ids():
    cats = [Row(1, "Groceries")]
    accts = [Row(7, "Checking")]
    out = _normalize(_parsed(category="groceries", account="Checking"),
                     cats, accts, date(2026, 6, 21))
    assert out["transaction_type"] == "expense"
    assert out["amount"] == 42.5
    assert out["category_id"] == 1
    assert out["account_id"] == 7
    assert out["transaction_date"] == "2026-06-20"


def test_normalize_validates_type_and_amount():
    out = _normalize(_parsed(transaction_type="WEIRD", amount=-3),
                     [], [], date(2026, 6, 21))
    assert out["transaction_type"] == "expense"   # invalid type → expense
    assert out["amount"] is None                  # non-positive → None


def test_normalize_bad_date_falls_back_to_today():
    out = _normalize(_parsed(transaction_date="not-a-date"),
                     [], [], date(2026, 6, 21))
    assert out["transaction_date"] == "2026-06-21"


def test_normalize_unmatched_category_is_none():
    out = _normalize(_parsed(category="Nope"), [Row(1, "Groceries")], [],
                     date(2026, 6, 21))
    assert out["category_id"] is None


# --- route: auth + fragment shape ------------------------------------------

def test_parse_requires_login(anon_client):
    resp = anon_client.post("/transactions/parse", data={"text": "x"})
    assert resp.status_code == 302


def test_parse_returns_prefilled_form_fragment(client_a, users, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_call_model",
                        lambda *a, **k: _parsed(category="cat-A", account="acct-A"))
    resp = client_a.post("/transactions/parse",
                         data={"text": "spent 42.5 on groceries"}, headers=HX)
    assert resp.status_code == 200
    assert b"<html" not in resp.data          # a fragment, not a full page
    assert b"<form" in resp.data
    assert b'value="42.5"' in resp.data
    # the user's own seeded category/account come back selected
    cat_id = str(users["a"]["category_id"]).encode()
    assert b'value="' + cat_id + b'" selected' in resp.data


# --- route: per-user isolation ---------------------------------------------

def test_parse_only_matches_own_rows(client_a, users, monkeypatch):
    # Model names user B's category/account; resolution must not leak them to A.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_call_model",
                        lambda *a, **k: _parsed(category="cat-B", account="acct-B"))
    resp = client_a.post("/transactions/parse",
                         data={"text": "spent 10"}, headers=HX)
    assert resp.status_code == 200
    assert b"cat-B" not in resp.data          # B's category never in A's form
    assert b"acct-B" not in resp.data
    # A's own category exists but must NOT be selected (model named cat-B)
    cat_a = str(users["a"]["category_id"]).encode()
    assert b'value="' + cat_a + b'" selected' not in resp.data


# --- route: graceful fallback ----------------------------------------------

def test_parse_no_api_key_falls_back(client_a, users, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = client_a.post("/transactions/parse",
                         data={"text": "spent 10"}, headers=HX)
    assert resp.status_code == 200
    assert b"<form" in resp.data              # manual form still usable
    assert "showToast" in resp.headers.get("HX-Trigger", "")


def test_parse_api_error_falls_back(client_a, users, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def boom(*a, **k):
        raise ParseError("network down")
    monkeypatch.setattr(ai, "_call_model", boom)
    resp = client_a.post("/transactions/parse",
                         data={"text": "spent 10"}, headers=HX)
    assert resp.status_code == 200
    assert b"<form" in resp.data
    assert "showToast" in resp.headers.get("HX-Trigger", "")


def test_parse_empty_text_prompts(client_a, users):
    resp = client_a.post("/transactions/parse", data={"text": "  "}, headers=HX)
    assert resp.status_code == 200
    assert b"<form" in resp.data
    assert "showToast" in resp.headers.get("HX-Trigger", "")
