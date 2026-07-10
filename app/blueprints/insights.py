"""v10.1 — monthly "Insight" digest (Budget Buddy's second AI feature).

An on-demand, cached AI recap of the current month shown at the top of the
dashboard. The split of responsibility is the whole point:

  * compute_month_facts() produces every NUMBER, deterministically, from the
    user's own rows (reusing compute_budget_vs_actual for overruns). This is the
    only source of figures.
  * app.ai.generate_insight() turns those facts into prose + tips — it never
    does arithmetic, so nothing it returns is trusted as a figure.

The result is cached one-row-per-(user, month) in `insights` so it shows
instantly on later loads; "Regenerate" overwrites via the unique upsert. The
feature is gated on ai_enabled() and degrades gracefully (an error toast, never
a broken page) when the key/model is unavailable.
"""
import json
from datetime import datetime

from flask import Blueprint, render_template, request, make_response
from flask_login import login_required, current_user

from app import limiter
from app.ai import generate_insight, ParseError, MODEL
from app.db import db_cursor
from app.helpers import hx_toast
from app.blueprints.budgets import compute_budget_vs_actual
from app.blueprints.accounts import credit_card_utilization_facts

bp = Blueprint('insights', __name__)


def _prev_month(year, month):
    """The calendar month before (year, month)."""
    if month == 1:
        return year - 1, 12
    return year, month - 1


def category_spending(cursor, user_id, year, month, limit=None):
    """Expense spending grouped by category for one month (non-adjustment,
    non-transfer), highest first → [(name, total_float), ...]. The single source
    for the per-category rollup, reused by the dashboard digest and the Ask tool
    so they can never disagree on what counts."""
    sql = """
        SELECT c.name, SUM(t.amount) AS total
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        WHERE t.user_id = %s AND t.transaction_type = 'expense'
        AND t.is_adjustment = false AND t.is_transfer = false
        AND EXTRACT(YEAR FROM t.transaction_date) = %s
        AND EXTRACT(MONTH FROM t.transaction_date) = %s
        GROUP BY c.name
        ORDER BY total DESC
    """
    params = [user_id, year, month]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    cursor.execute(sql, params)
    return [(r[0], float(r[1])) for r in cursor.fetchall()]


def _income_expense(cursor, user_id, year, month):
    """(income, expenses) totals for one month — non-adjustment, non-transfer,
    matching how the dashboard and analytics count real cash flow."""
    cursor.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE 0 END), 0) AS income,
            COALESCE(SUM(CASE WHEN transaction_type = 'expense' THEN amount ELSE 0 END), 0) AS expenses
        FROM transactions
        WHERE user_id = %s AND is_adjustment = false AND is_transfer = false
        AND EXTRACT(YEAR FROM transaction_date) = %s
        AND EXTRACT(MONTH FROM transaction_date) = %s
    """, (user_id, year, month))
    income, expenses = cursor.fetchone()
    return float(income), float(expenses)


def compute_month_facts(user_id, year, month):
    """Deterministic figure builder for one month — the ONLY source of numbers
    the digest describes. Reads the user's own rows only. Returns:

        {year, month, income, expenses, net, savings_rate,
         prev: {income, expenses, net},
         top_categories: [{name, amount}, ...],   # this month's top expenses
         overruns: [{category, budget, actual, over}, ...],  # actual > budget
         credit_cards: [{name, limit, debt, available, utilization_pct}, ...]}
           # v10.10 — CURRENT card utilization snapshot (cards with a limit
           # set only; always present, [] when none)
    """
    year, month = int(year), int(month)
    with db_cursor() as cursor:
        income, expenses = _income_expense(cursor, user_id, year, month)
        py, pm = _prev_month(year, month)
        p_income, p_expenses = _income_expense(cursor, user_id, py, pm)

        top_categories = [{'name': n, 'amount': a} for n, a in
                          category_spending(cursor, user_id, year, month, limit=5)]

    # Budget overruns reuse the shared month-based helper (its own connection).
    overruns = []
    for category, budget, actual, remaining in \
            compute_budget_vs_actual(user_id, year, month):
        if float(remaining) < 0:
            overruns.append({
                'category': category,
                'budget': float(budget),
                'actual': float(actual),
                'over': round(float(actual) - float(budget), 2),
            })

    net = round(income - expenses, 2)
    savings_rate = round((income - expenses) / income * 100, 1) if income > 0 else None
    return {
        'year': year,
        'month': month,
        'income': round(income, 2),
        'expenses': round(expenses, 2),
        'net': net,
        'savings_rate': savings_rate,
        'prev': {
            'income': round(p_income, 2),
            'expenses': round(p_expenses, 2),
            'net': round(p_income - p_expenses, 2),
        },
        'top_categories': top_categories,
        'overruns': overruns,
        'credit_cards': credit_card_utilization_facts(user_id),
    }


def load_insight(cursor, user_id, year, month):
    """Return the cached digest for (user, month) as {summary, tips, created_at},
    or None. Takes an existing cursor so the dashboard reuses its connection."""
    cursor.execute("""
        SELECT content, created_at FROM insights
        WHERE user_id = %s AND year = %s AND month = %s
    """, (user_id, int(year), int(month)))
    row = cursor.fetchone()
    if row is None:
        return None
    data = json.loads(row[0])
    data['created_at'] = row[1]
    return data


@bp.route('/insights/generate', methods=['POST'])
@limiter.limit("10 per minute")
@login_required
def generate():
    """Generate (or Regenerate) the current month's digest: compute the facts,
    have Claude narrate them once, cache the result, and swap the card.

    Page-agnostic HTMX endpoint — the dashboard card posts here and swaps itself
    (#insight-card) with the returned fragment. Any failure falls back to the
    un-generated card + an error toast, so the dashboard never breaks."""
    today = datetime.today()
    year = request.form.get('year', type=int) or today.year
    month = request.form.get('month', type=int) or today.month
    if not (1 <= month <= 12 and 1 <= year <= 9999):
        # Tamperable hidden fields (the forecasts twin's clamp) — fall back to now.
        year, month = today.year, today.month
    facts = compute_month_facts(current_user.id, year, month)

    def _card(insight):
        return make_response(render_template(
            'partials/_insight_card.html',
            insight=insight, facts=facts,
            insight_year=year, insight_month=month, ai_enabled=True))

    if facts['income'] == 0 and facts['expenses'] == 0:
        return hx_toast(_card(None), 'Not enough data for this month yet', 'error')

    try:
        result = generate_insight(facts)
    except ParseError:
        return hx_toast(_card(None), "Couldn't generate an insight right now", 'error')

    with db_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO insights (user_id, year, month, content, model)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id, year, month)
            DO UPDATE SET content = EXCLUDED.content, model = EXCLUDED.model,
                          created_at = now()
        """, (current_user.id, year, month, json.dumps(result), MODEL))

    insight = dict(result)
    insight['created_at'] = today
    return hx_toast(_card(insight), 'Insight ready')
