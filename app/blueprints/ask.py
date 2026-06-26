"""v10.3 — "Ask your finances" (tool-use, Budget Buddy's fourth AI feature).

A plain-English question box (on /analytics) answered from the user's REAL data.
The model orchestrates; the app computes. This module is the **security
boundary**: it defines a small set of read-only, per-user-scoped, fixed-
parameterized query tools and a `dispatch` that validates every argument before
running one. `app/ai.py` drives the multi-turn loop but never touches the DB and
is never handed a user id — so the model can't reach another user's rows or
mutate anything, because the tools simply don't allow it.

Each tool handler runs ONE fixed query scoped `WHERE user_id = %s` (forced to the
caller's id), reusing the same deterministic helpers the rest of the app uses.
Args are sanitized in `dispatch` before the query: months/dates are parsed with
strptime, limits are clamped, and a category name is resolved ONLY against the
user's own rows (like the v9 parser's _match_id). Nothing is ever interpolated
into SQL. Gated on ai_enabled() and degrades gracefully (an error fragment +
toast, never a broken page) when the key/model is unavailable.
"""
from datetime import date, datetime

from flask import Blueprint, render_template, request, make_response
from flask_login import login_required, current_user

from app import limiter
from app.ai import answer_question, ParseError
from app.db import db_cursor
from app.helpers import hx_toast
from app.blueprints.insights import compute_month_facts
from app.blueprints.budgets import compute_budget_vs_actual

bp = Blueprint('ask', __name__)

# Bound the rows any single tool can hand back to the model (keeps token cost and
# answer scope sane; the model never sees more than this).
MAX_LIMIT = 25


class _ToolError(Exception):
    """A bad argument or unresolved name. dispatch turns it into an is_error
    tool_result so the model can correct itself or decline honestly — never a
    500."""


# --- pure argument validators (the trust boundary) --------------------------

def _parse_month(args):
    """Pull and validate a 'YYYY-MM' arg → (year, month). Raises _ToolError."""
    raw = str(args.get('month', '')).strip()
    try:
        d = datetime.strptime(raw, '%Y-%m').date()
    except (TypeError, ValueError):
        raise _ToolError(f"month must be 'YYYY-MM', got {raw!r}")
    return d.year, d.month


def _parse_date(args, key):
    """Pull and validate a 'YYYY-MM-DD' arg → date. Raises _ToolError."""
    raw = str(args.get(key, '')).strip()
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        raise _ToolError(f"{key} must be 'YYYY-MM-DD', got {raw!r}")


def _clamp_limit(args, default=10):
    """Coerce a 'limit' arg to int and clamp to 1..MAX_LIMIT."""
    try:
        n = int(args.get('limit', default))
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, MAX_LIMIT))


def _resolve_category(cursor, user_id, name):
    """Match `name` (case-insensitive) against the user's OWN categories → id.
    Raises _ToolError listing the valid names so the model can retry — this is
    the per-user guard that the model can only name a category the user owns."""
    cursor.execute("SELECT id, name FROM categories WHERE user_id = %s", (user_id,))
    rows = cursor.fetchall()
    target = str(name or '').strip().lower()
    for cid, cname in rows:
        if cname.strip().lower() == target:
            return cid, cname
    valid = ', '.join(sorted(r[1] for r in rows)) or '(none)'
    raise _ToolError(f"No category named {name!r}. Your categories are: {valid}")


# --- tool handlers (each: ONE fixed, user-scoped query) ---------------------

def _t_list_categories(user_id, args):
    with db_cursor() as cursor:
        cursor.execute("SELECT name FROM categories WHERE user_id = %s ORDER BY name",
                       (user_id,))
        names = [r[0] for r in cursor.fetchall()]
    return {"categories": names}


def _t_spending_by_category(user_id, args):
    year, month = _parse_month(args)
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT c.name, SUM(t.amount) AS total
            FROM transactions t
            JOIN categories c ON t.category_id = c.id
            WHERE t.user_id = %s AND t.transaction_type = 'expense'
            AND t.is_adjustment = false AND t.is_transfer = false
            AND EXTRACT(YEAR FROM t.transaction_date) = %s
            AND EXTRACT(MONTH FROM t.transaction_date) = %s
            GROUP BY c.name
            ORDER BY total DESC
        """, (user_id, year, month))
        by_cat = [{"category": r[0], "total": round(float(r[1]), 2)}
                  for r in cursor.fetchall()]
    return {"month": f"{year}-{month:02d}", "spending_by_category": by_cat}


def _t_income_expense_summary(user_id, args):
    year, month = _parse_month(args)
    facts = compute_month_facts(user_id, year, month)
    return {
        "month": f"{year}-{month:02d}",
        "income": facts["income"],
        "expenses": facts["expenses"],
        "net": facts["net"],
        "savings_rate_pct": facts["savings_rate"],
        "top_categories": facts["top_categories"],
    }


def _t_budget_status(user_id, args):
    year, month = _parse_month(args)
    rows = compute_budget_vs_actual(user_id, year, month)
    budgets = [{
        "category": cat,
        "budget": round(float(budget), 2),
        "actual": round(float(actual), 2),
        "remaining": round(float(remaining), 2),
        "over_budget": float(remaining) < 0,
    } for cat, budget, actual, remaining in rows]
    return {"month": f"{year}-{month:02d}", "budgets": budgets}


def _t_total_for_category(user_id, args):
    start = _parse_date(args, 'start_date')
    end = _parse_date(args, 'end_date')
    with db_cursor() as cursor:
        cid, cname = _resolve_category(cursor, user_id, args.get('category'))
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) FROM transactions
            WHERE user_id = %s AND category_id = %s AND transaction_type = 'expense'
            AND is_adjustment = false AND is_transfer = false
            AND transaction_date >= %s AND transaction_date <= %s
        """, (user_id, cid, start, end))
        total = float(cursor.fetchone()[0])
    return {"category": cname, "start_date": start.isoformat(),
            "end_date": end.isoformat(), "total_spent": round(total, 2)}


def _t_search_transactions(user_id, args):
    start = _parse_date(args, 'start_date')
    end = _parse_date(args, 'end_date')
    limit = _clamp_limit(args)
    text = str(args.get('text', '')).strip()
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT t.transaction_date, t.description, t.amount, t.transaction_type,
                   c.name
            FROM transactions t
            LEFT JOIN categories c ON t.category_id = c.id
            WHERE t.user_id = %s AND t.is_transfer = false
            AND t.description ILIKE %s
            AND t.transaction_date >= %s AND t.transaction_date <= %s
            ORDER BY t.transaction_date DESC
            LIMIT %s
        """, (user_id, f'%{text}%', start, end, limit))
        txns = [{
            "date": r[0].isoformat() if r[0] else None,
            "description": r[1],
            "amount": round(float(r[2]), 2),
            "type": r[3],
            "category": r[4],
        } for r in cursor.fetchall()]
    return {"matches": txns, "count": len(txns)}


def _t_upcoming_scheduled(user_id, args):
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT description, amount, transaction_type, frequency, next_due
            FROM schedules
            WHERE user_id = %s AND is_active = true
            ORDER BY next_due
        """, (user_id,))
        items = [{
            "description": r[0],
            "amount": round(float(r[1]), 2),
            "type": r[2],
            "frequency": r[3],
            "next_due": r[4].isoformat() if r[4] else None,
        } for r in cursor.fetchall()]
    return {"scheduled": items}


def _t_account_balances(user_id, args):
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT a.account_name,
                   COALESCE(SUM(CASE WHEN t.transaction_type = 'income'
                                     THEN t.amount ELSE -t.amount END), 0) AS balance
            FROM account a
            LEFT JOIN transactions t ON a.account_id = t.account_id
                AND t.user_id = a.user_id
            WHERE a.user_id = %s
            GROUP BY a.account_id, a.account_name
            ORDER BY balance DESC
        """, (user_id,))
        accts = [{"account": r[0], "balance": round(float(r[1]), 2)}
                 for r in cursor.fetchall()]
    return {"accounts": accts}


# --- tool definitions (the schemas the model sees) --------------------------
# strict + additionalProperties:false + required → args validate exactly.

def _no_args():
    return {"type": "object", "properties": {}, "required": [],
            "additionalProperties": False}


def _month_arg():
    return {
        "type": "object",
        "properties": {"month": {"type": "string",
                                 "description": "Month as YYYY-MM, e.g. 2026-05"}},
        "required": ["month"],
        "additionalProperties": False,
    }


# (json schema spec, handler). The spec name must match the registry key.
ASK_TOOLS = [
    ({"name": "list_categories",
      "description": "List the user's own expense/income category names. Call this "
                     "first when you need the exact category name to pass to "
                     "another tool.",
      "strict": True, "input_schema": _no_args()},
     _t_list_categories),

    ({"name": "spending_by_category",
      "description": "Total expense spending broken down by category for one "
                     "month. Use for 'what did I spend on X' or 'where did my "
                     "money go' questions.",
      "strict": True, "input_schema": _month_arg()},
     _t_spending_by_category),

    ({"name": "income_expense_summary",
      "description": "Income, expenses, net, savings rate, and top categories for "
                     "one month. Use for 'how did I do' / 'am I in the black' "
                     "questions.",
      "strict": True, "input_schema": _month_arg()},
     _t_income_expense_summary),

    ({"name": "budget_status",
      "description": "Budget vs actual spending per category for one month, "
                     "including which categories are over budget. Use for 'am I "
                     "on track / over budget' questions.",
      "strict": True, "input_schema": _month_arg()},
     _t_budget_status),

    ({"name": "total_for_category",
      "description": "Total expense spending in one category over a date range. "
                     "Pass an exact category name (call list_categories if "
                     "unsure).",
      "strict": True, "input_schema": {
          "type": "object",
          "properties": {
              "category": {"type": "string", "description": "Exact category name"},
              "start_date": {"type": "string", "description": "YYYY-MM-DD"},
              "end_date": {"type": "string", "description": "YYYY-MM-DD"},
          },
          "required": ["category", "start_date", "end_date"],
          "additionalProperties": False,
      }},
     _t_total_for_category),

    ({"name": "search_transactions",
      "description": "Search the user's transactions whose description matches "
                     "some text, within a date range (newest first, capped). Use "
                     "for 'did I pay X' / 'find my Y purchases' questions.",
      "strict": True, "input_schema": {
          "type": "object",
          "properties": {
              "text": {"type": "string", "description": "Text to match in the "
                                                        "description"},
              "start_date": {"type": "string", "description": "YYYY-MM-DD"},
              "end_date": {"type": "string", "description": "YYYY-MM-DD"},
              "limit": {"type": "integer",
                        "description": f"Max rows (1-{MAX_LIMIT})"},
          },
          "required": ["text", "start_date", "end_date"],
          "additionalProperties": False,
      }},
     _t_search_transactions),

    ({"name": "upcoming_scheduled",
      "description": "List the user's active recurring schedules (income & bills) "
                     "with their amounts and next due dates. Use for 'what bills "
                     "are coming up' / 'biggest recurring bill' questions.",
      "strict": True, "input_schema": _no_args()},
     _t_upcoming_scheduled),

    ({"name": "account_balances",
      "description": "Current balance of each of the user's accounts. Use for "
                     "'how much do I have' / 'account balance' questions.",
      "strict": True, "input_schema": _no_args()},
     _t_account_balances),
]

_HANDLERS = {spec["name"]: handler for spec, handler in ASK_TOOLS}
TOOL_SPECS = [spec for spec, _ in ASK_TOOLS]


def dispatch(user_id, name, raw_input):
    """Run one tool for `user_id`, validating args first. Returns
    (content_str, is_error). user_id is forced by the caller — the model never
    supplies it. Unknown tool or bad args → an is_error result the model can
    recover from, never a crash."""
    import json
    handler = _HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"Unknown tool: {name}"}), True
    try:
        result = handler(user_id, raw_input or {})
        return json.dumps(result, default=str), False
    except _ToolError as e:
        return json.dumps({"error": str(e)}), True
    except Exception:
        return json.dumps({"error": "That query couldn't be run."}), True


@bp.route('/ask', methods=['POST'])
@limiter.limit("10 per minute")
@login_required
def ask():
    """Answer a free-text finance question from the user's own data.

    Page-agnostic HTMX endpoint — the box on /analytics posts here and swaps the
    answer into #ask-answer. Any failure falls back to a graceful decline + an
    error toast, so the page never breaks."""
    question = request.form.get('question', '').strip()

    def _answer(result):
        return make_response(render_template(
            'partials/_ask_answer.html', result=result))

    if not question:
        return hx_toast(_answer(None), 'Type a question first', 'error')

    # Bind the user id into the dispatch callback — ai.py never sees it.
    def _dispatch(tool_name, raw_input):
        return dispatch(current_user.id, tool_name, raw_input)

    try:
        result = answer_question(question, TOOL_SPECS, _dispatch, today=date.today())
    except ParseError:
        return hx_toast(_answer(None), "Couldn't answer that right now", 'error')

    return _answer(result)
