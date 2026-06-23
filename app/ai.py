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
