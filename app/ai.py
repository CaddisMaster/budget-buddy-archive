"""v9.0 — natural-language transaction parsing via Claude.

A single isolated entry point, parse_transaction_text(), turns a free-text note
("spent 42 on groceries at Safeway yesterday") into structured transaction
fields, then validates and resolves them against the user's own categories and
accounts. Kept apart from the blueprint so it stays unit-testable (tests
monkeypatch parse_transaction_text) and so a missing ANTHROPIC_API_KEY — or the
anthropic package not being installed — never breaks app import.
"""
import json
import os
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel

# Cheap model on purpose — Sean's explicit cost call for this feature. The whole
# point of NL quick-add is pennies-per-parse, so don't "upgrade" to Opus/Sonnet.
MODEL = "claude-haiku-4-5"


class ParseError(Exception):
    """Raised on any failure (no key, package missing, API error, bad output).
    The caller falls back to the empty manual form, so the feature degrades
    gracefully instead of erroring the page."""


class _ParsedTransaction(BaseModel):
    """The shape we ask Claude to return (structured outputs). Everything here
    is treated as untrusted and re-validated in _normalize()."""
    transaction_type: str            # "expense" | "income"
    amount: float
    description: str
    category: Optional[str]          # one of the user's category names, or null
    account: Optional[str]           # one of the user's account names, or null
    transaction_date: str            # YYYY-MM-DD


def parse_transaction_text(text, categories, accounts, *, today=None):
    """Parse free-text into resolved, ready-to-prefill transaction fields.

    `categories` is the user's own rows as (id, name) tuples and `accounts` as
    (account_id, name) tuples — the same shape new_transaction already loads.
    Returns:

        {transaction_type, amount, description,
         category_id, account_id, transaction_date}

    category_id / account_id are None when the model didn't name a row the user
    actually owns (validated server-side, case-insensitive). amount is None when
    the model didn't give a usable positive number. Raises ParseError on any
    failure so the caller can fall back to the manual form.
    """
    today = today or date.today()
    text = (text or "").strip()
    if not text:
        raise ParseError("No text to parse")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ParseError("ANTHROPIC_API_KEY is not set")

    category_names = [c[1] for c in categories]
    account_names = [a[1] for a in accounts]

    parsed = _call_model(text, category_names, account_names, today, api_key)
    if parsed is None:
        raise ParseError("Model returned no structured output")

    return _normalize(parsed, categories, accounts, today)


def _call_model(text, category_names, account_names, today, api_key):
    """The single network call to Claude — isolated so tests can stub it without
    hitting the API. Returns a _ParsedTransaction (or None); wraps any SDK,
    network, or missing-package error in ParseError."""
    system = (
        "You convert a short free-text note into a single personal-finance "
        f"transaction. Today's date is {today.isoformat()}; resolve relative "
        "dates like 'yesterday' or 'last friday' to an absolute YYYY-MM-DD "
        "date. Use transaction_type 'income' for money received and 'expense' "
        "for money spent. amount is a positive number with no currency symbol. "
        "Pick category and account ONLY from the provided lists, matching the "
        "user's wording to the closest option; if nothing fits, use null. Write "
        "a short, human description of what the money was for.\n"
        f"Categories: {category_names}\n"
        f"Accounts: {account_names}"
    )
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.parse(
            model=MODEL,
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": text}],
            output_format=_ParsedTransaction,
        )
        return response.parsed_output
    except Exception as e:  # network, auth, malformed output, missing package
        raise ParseError(str(e)) from e


def _normalize(parsed, categories, accounts, today):
    """Coerce/validate the model's output into safe form values. Pure — no API,
    so the resolution and validation logic is directly unit-testable."""
    ttype = (parsed.transaction_type or "").strip().lower()
    if ttype not in ("income", "expense"):
        ttype = "expense"

    try:
        amount = round(float(parsed.amount), 2)
        if amount <= 0:
            amount = None
    except (TypeError, ValueError):
        amount = None

    description = (parsed.description or "").strip()

    txn_date = today.isoformat()
    if parsed.transaction_date:
        try:
            txn_date = (
                datetime.strptime(parsed.transaction_date.strip(), "%Y-%m-%d")
                .date()
                .isoformat()
            )
        except (TypeError, ValueError):
            pass  # leave as today on anything unparseable

    return {
        "transaction_type": ttype,
        "amount": amount,
        "description": description,
        "category_id": _match_id(parsed.category, categories),
        "account_id": _match_id(parsed.account, accounts),
        "transaction_date": txn_date,
    }


def _match_id(name, rows):
    """Case-insensitive exact match of `name` against (id, name) rows → id, else
    None. This is the server-side guard that the model can only select a row the
    current user actually owns."""
    if not name:
        return None
    target = str(name).strip().lower()
    for row in rows:
        if str(row[1]).strip().lower() == target:
            return row[0]
    return None


# ---------------------------------------------------------------------------
# v10.1 — monthly "Insight" digest.
#
# The same isolated-seam pattern as the v9 parser, but for summarization: the
# app computes every figure deterministically (compute_month_facts) and hands
# them to Claude as JSON; the model only writes a plain-English recap + a tip or
# two. It must NOT recompute or invent numbers — so nothing the model returns is
# ever used as a figure, only as prose. Kept in this module so a missing key or
# package degrades gracefully (ParseError) instead of breaking the dashboard.
# ---------------------------------------------------------------------------

class _Insight(BaseModel):
    """The narrative shape we ask Claude to return (structured outputs). Treated
    as untrusted text — coerced/trimmed in generate_insight()."""
    summary: str            # 2–3 sentence plain-English recap
    tips: list[str]         # 1–2 short coaching tips


def generate_insight(facts, *, today=None):
    """Turn already-computed monthly figures into a short narrative digest.

    `facts` is the deterministic dict from compute_month_facts(). Returns
    {"summary": str, "tips": [str, ...]} (tips capped at 2). Raises ParseError on
    any failure (no key, package missing, API/output error) so the caller can
    fall back to the un-generated card + an error toast.
    """
    today = today or date.today()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ParseError("ANTHROPIC_API_KEY is not set")

    parsed = _call_insight_model(facts, today, api_key)
    if parsed is None:
        raise ParseError("Model returned no structured output")

    summary = (parsed.summary or "").strip()
    if not summary:
        raise ParseError("Model returned an empty summary")
    tips = [t.strip() for t in (parsed.tips or []) if t and t.strip()][:2]
    return {"summary": summary, "tips": tips}


def _call_insight_model(facts, today, api_key):
    """The single network call for the digest — isolated so tests stub it without
    hitting the API. Returns an _Insight (or None); wraps any SDK, network, or
    missing-package error in ParseError."""
    system = (
        "You are a friendly, encouraging personal-finance coach writing a short "
        "monthly digest. You are given the month's already-computed figures as "
        "JSON. Do NOT recompute, re-add, or invent any numbers — treat the "
        "figures as ground truth and only describe them. Write a warm 2-3 "
        "sentence plain-English recap of how the month is going (income vs "
        "spending, net, notable categories or budget overruns), then 1-2 short, "
        "specific, actionable tips. Be supportive, not preachy. Today is "
        f"{today.isoformat()} and the month may still be in progress."
    )
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.parse(
            model=MODEL,
            max_tokens=400,
            system=system,
            messages=[{"role": "user", "content": json.dumps(facts, default=str)}],
            output_format=_Insight,
        )
        return response.parsed_output
    except Exception as e:  # network, auth, malformed output, missing package
        raise ParseError(str(e)) from e


# ---------------------------------------------------------------------------
# v10.2 — month-ahead "Forecast" projection.
#
# The capstone of the AI arc and the twin of Insight: where Insight recaps the
# month behind you, Forecast looks forward. The app computes every figure
# deterministically (compute_forecast — month-to-date actuals, a day-weighted
# spend projection, and remaining scheduled income) and hands them to Claude as
# JSON; the model only narrates the outlook + a tip or two. Same isolated-seam,
# graceful-degradation contract as Insight — it must NOT recompute or invent
# numbers, and a missing key/package degrades to a ParseError, never a broken
# dashboard.
# ---------------------------------------------------------------------------

class _Forecast(BaseModel):
    """The narrative shape we ask Claude to return (structured outputs). Treated
    as untrusted text — coerced/trimmed in generate_forecast()."""
    summary: str            # 2–3 sentence plain-English month-ahead outlook
    tips: list[str]         # 1–2 short, forward-looking tips


def generate_forecast(facts, *, today=None):
    """Turn already-computed projection figures into a short forward-looking
    narrative.

    `facts` is the deterministic dict from compute_forecast(). Returns
    {"summary": str, "tips": [str, ...]} (tips capped at 2). Raises ParseError on
    any failure (no key, package missing, API/output error) so the caller can
    fall back to the un-generated card + an error toast.
    """
    today = today or date.today()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ParseError("ANTHROPIC_API_KEY is not set")

    parsed = _call_forecast_model(facts, today, api_key)
    if parsed is None:
        raise ParseError("Model returned no structured output")

    summary = (parsed.summary or "").strip()
    if not summary:
        raise ParseError("Model returned an empty summary")
    tips = [t.strip() for t in (parsed.tips or []) if t and t.strip()][:2]
    return {"summary": summary, "tips": tips}


def _call_forecast_model(facts, today, api_key):
    """The single network call for the forecast — isolated so tests stub it
    without hitting the API. Returns a _Forecast (or None); wraps any SDK,
    network, or missing-package error in ParseError."""
    system = (
        "You are a friendly, encouraging personal-finance coach writing a short "
        "month-AHEAD forecast. You are given the month's already-computed "
        "projection figures as JSON (month-to-date actuals, a projected "
        "end-of-month income/expenses/net, remaining scheduled items, and budget "
        "totals). Do NOT recompute, re-add, or invent any numbers — treat the "
        "figures as ground truth and only describe them. Write a warm 2-3 "
        "sentence plain-English outlook for the rest of the month (where net is "
        "headed, whether spending is on pace vs budget, the next notable "
        "scheduled income or bill), then 1-2 short, specific, forward-looking "
        "tips. Be supportive, not preachy. Today is "
        f"{today.isoformat()} and the month is still in progress, so frame it as "
        "a projection ('on pace to…', 'you're likely to…'), not a fact."
    )
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.parse(
            model=MODEL,
            max_tokens=400,
            system=system,
            messages=[{"role": "user", "content": json.dumps(facts, default=str)}],
            output_format=_Forecast,
        )
        return response.parsed_output
    except Exception as e:  # network, auth, malformed output, missing package
        raise ParseError(str(e)) from e


# ---------------------------------------------------------------------------
# v10.3 — "Ask your finances" (tool-use).
#
# The capstone LLM technique and the only one not built before: multi-turn tool
# use. The user asks a plain-English question; the model answers ONLY by calling
# the app's read-only, per-user-scoped query tools — it never computes or invents
# a figure, it calls a tool and reports what the tool returns. Same split of
# responsibility as Insight/Forecast, but multi-turn:
#
#   model -> tool_use -> app runs the tool -> tool_result -> model -> answer
#
# The security boundary is the tool surface, which lives in blueprints/ask.py
# (where current_user.id is bound). This module owns ONLY the conversation loop
# and its single isolated network seam — it never touches the DB and is never
# handed a user id. `dispatch` is a callback the blueprint supplies (a closure
# over the current user) that validates args and runs one fixed query.
# ---------------------------------------------------------------------------

ASK_MODEL = MODEL          # Haiku — Sean's cost call, same as the other AI beats.
ASK_MAX_TURNS = 6          # hard cap on model<->tool round-trips (loop guard).


def answer_question(question, tool_specs, dispatch, *, today=None):
    """Answer a free-text finance question by letting Claude orchestrate the
    read-only query tools and narrate the result.

    `tool_specs` is the list of tool JSON definitions; `dispatch(name, raw_input)`
    is the blueprint's per-user callback returning a (content_str, is_error)
    tuple for each tool call. Returns {"answer": str, "tools_used": [name, ...]}.
    Raises ParseError on any failure (no key/package, API error, empty answer,
    or exceeding the turn cap) so the caller can fall back gracefully.
    """
    today = today or date.today()
    question = (question or "").strip()
    if not question:
        raise ParseError("No question to answer")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ParseError("ANTHROPIC_API_KEY is not set")

    messages = [{"role": "user", "content": question}]
    tools_used = []

    for _ in range(ASK_MAX_TURNS):
        response = _call_ask_model(messages, tool_specs, today, api_key)
        if response is None:
            raise ParseError("Model returned no response")

        blocks = list(response.content or [])
        tool_uses = [b for b in blocks if getattr(b, "type", None) == "tool_use"]

        # Drive purely off the presence of tool_use blocks — the actual contract.
        # (stop_reason can disagree with the block list on a malformed turn; acting
        # on it would post an empty tool_result message the API then rejects.)
        if not tool_uses:
            text = " ".join(
                b.text.strip() for b in blocks
                if getattr(b, "type", None) == "text" and getattr(b, "text", "")
            ).strip()
            if not text:
                raise ParseError("Model returned an empty answer")
            return {"answer": text, "tools_used": tools_used}

        # Echo the assistant turn (incl. its tool_use blocks) back, then run each
        # tool and return ALL results in a single user message (the API contract
        # for parallel tool calls).
        messages.append({"role": "assistant", "content": blocks})
        results = []
        for tu in tool_uses:
            content, is_error = dispatch(tu.name, tu.input)
            # Only credit a tool that actually returned data — a failed call the
            # model recovered from shouldn't show in the "Computed from your data"
            # footer.
            if not is_error:
                tools_used.append(tu.name)
            results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": content,
                "is_error": is_error,
            })
        messages.append({"role": "user", "content": results})

    raise ParseError("Exceeded tool-use turn limit")


_ASK_SYSTEM = (
    "You answer questions about the user's OWN personal finances. You may "
    "answer ONLY by calling the provided tools — never compute, estimate, or "
    "invent a figure yourself; call a tool and report exactly what it "
    "returns. Use list_categories first if you need the user's category "
    "names. If the available tools cannot answer the question, say so plainly "
    "rather than guessing. Keep the final answer concise, friendly, and in "
    "plain English — write plain text only, with NO Markdown, asterisks, "
    "bullet syntax, or other formatting. Today's date is {today}."
)

# One client per api_key, reused across the up-to-6 turns of a question (and
# across questions) so the loop keeps the httpx keep-alive pool warm instead of
# re-doing the TLS handshake to api.anthropic.com on every turn.
_ask_clients = {}


def _get_ask_client(api_key):
    client = _ask_clients.get(api_key)
    if client is None:
        import anthropic
        # Bound each call so a stuck request fails fast into the graceful fallback
        # rather than hanging the loop toward the gunicorn worker timeout.
        client = anthropic.Anthropic(api_key=api_key, timeout=30.0)
        _ask_clients[api_key] = client
    return client


def _call_ask_model(messages, tool_specs, today, api_key):
    """The single network call for the ask loop — isolated so tests can stub it
    without hitting the API (feeding canned tool_use/text responses). Returns the
    raw Message (with .content and .stop_reason); wraps any SDK, network, or
    missing-package error in ParseError."""
    try:
        client = _get_ask_client(api_key)
        return client.messages.create(
            model=ASK_MODEL,
            max_tokens=1024,
            system=_ASK_SYSTEM.format(today=today.isoformat()),
            tools=tool_specs,
            messages=messages,
        )
    except Exception as e:  # network, auth, malformed output, missing package
        raise ParseError(str(e)) from e


# ---------------------------------------------------------------------------
# v10.5 — "Auto-Categorize & Cleanup" (batch classification).
#
# Budget Buddy's fifth AI feature and its batch-classification beat. The app
# picks the candidate rows (uncategorized, plus recent rows that may be
# mis-filed) and owns the write; the model only PROPOSES a category name +
# confidence per row. Same split and graceful-degradation contract as the other
# beats: one isolated network seam, a pure normalizer that re-resolves every
# proposed name against the user's OWN categories (so the model can never invent
# a category or reach another user's rows), and ParseError on any failure. The
# user bulk-confirms in a review list — nothing the model returns is ever applied
# automatically. Sean's deliberate cost call: this is the ONE feature on Sonnet
# (categorization accuracy is worth more than pennies here), unlike the Haiku
# beats above.
# ---------------------------------------------------------------------------

CATEGORIZE_MODEL = "claude-sonnet-4-6"
_CONFIDENCES = ("high", "medium", "low")


class _Suggestion(BaseModel):
    """One proposed categorization. Treated as untrusted — re-resolved and
    re-validated in _normalize_suggestions()."""
    id: int                       # the transaction id we asked about
    category: Optional[str]       # one of the user's category names, or null
    confidence: str               # "high" | "medium" | "low"


class _Suggestions(BaseModel):
    """The batch shape we ask Claude to return (structured outputs)."""
    suggestions: list[_Suggestion]


def classify_transactions(rows, categories, *, today=None):
    """Propose a category for each candidate transaction in one batched call.

    `rows` is the candidate payload — a list of dicts with at least
    {id, description, amount, type, current_category}. `categories` is the user's
    own rows as (id, name) tuples (the same shape new_transaction loads). Returns
    a list of resolved suggestions:

        [{id, category_id, category_name, confidence}, ...]

    Each category_id is matched ONLY against a category the user actually owns
    (server-side, case-insensitive); proposals naming nothing the user owns are
    dropped, as are ids outside the submitted set. Raises ParseError on any
    failure (no key, package missing, API/output error) so the caller can fall
    back to the un-scanned banner + an error toast.
    """
    today = today or date.today()
    if not rows:
        return []

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ParseError("ANTHROPIC_API_KEY is not set")

    category_names = [c[1] for c in categories]
    parsed = _call_categorize_model(rows, category_names, today, api_key)
    if parsed is None:
        raise ParseError("Model returned no structured output")

    valid_ids = {int(r["id"]) for r in rows}
    return _normalize_suggestions(parsed, categories, valid_ids)


def _normalize_suggestions(parsed, categories, valid_ids):
    """Coerce/validate the model's batch into safe, resolved suggestions. Pure —
    no API, so the resolution and validation logic is directly unit-testable.
    Drops any proposal whose category doesn't resolve to a row the user owns or
    whose id wasn't one we asked about."""
    out = []
    seen = set()
    for s in (parsed.suggestions or []):
        try:
            sid = int(s.id)
        except (TypeError, ValueError):
            continue
        if sid not in valid_ids or sid in seen:
            continue
        category_id = _match_id(s.category, categories)
        if category_id is None:
            continue  # model named nothing the user actually owns
        confidence = (s.confidence or "").strip().lower()
        if confidence not in _CONFIDENCES:
            confidence = "low"
        seen.add(sid)
        out.append({
            "id": sid,
            "category_id": category_id,
            "category_name": _name_for(category_id, categories),
            "confidence": confidence,
        })
    return out


def _name_for(category_id, categories):
    """The owned category's display name for a resolved id (None if not found)."""
    for row in categories:
        if row[0] == category_id:
            return row[1]
    return None


def _call_categorize_model(rows, category_names, today, api_key):
    """The single network call for batch classification — isolated so tests can
    stub it without hitting the API. Returns a _Suggestions (or None); wraps any
    SDK, network, or missing-package error in ParseError."""
    system = (
        "You categorize personal-finance transactions. You are given a JSON list "
        "of transactions (each with an id, description, amount, type, and the "
        "current category if any) and the user's available category names. For "
        "EACH transaction, choose the single best-fitting category from the "
        "provided list and return its exact name, or null if none fits — do NOT "
        "invent a category that isn't in the list. Set confidence to 'high', "
        "'medium', or 'low' based on how clearly the description maps to the "
        "category. Return one suggestion object per input transaction, echoing "
        "its id. Today's date is " + today.isoformat() + ".\n"
        "Available categories: " + json.dumps(category_names)
    )
    try:
        import anthropic
        # Bound the call so a stuck request fails fast into the graceful fallback
        # rather than drifting toward the gunicorn worker timeout.
        client = anthropic.Anthropic(api_key=api_key, timeout=60.0)
        response = client.messages.parse(
            model=CATEGORIZE_MODEL,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": json.dumps(rows, default=str)}],
            output_format=_Suggestions,
        )
        return response.parsed_output
    except Exception as e:  # network, auth, malformed output, missing package
        raise ParseError(str(e)) from e
