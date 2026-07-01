import json
from datetime import datetime
from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from app.db import get_db_connection
from app.helpers import ai_enabled
from app.blueprints.goals import build_goals_view
from app.blueprints.budgets import compute_budget_vs_actual
from app.blueprints.insights import load_insight, compute_month_facts, _prev_month
from app.blueprints.forecasts import load_forecast, compute_forecast

bp = Blueprint('main', __name__)


@bp.route('/')
@login_required
def index():
    return render_template('index.html', ai_enabled=ai_enabled())


@bp.route('/dashboard')
@login_required
def dashboard():
    from app.blueprints.schedules import run_due_schedules  # lazy: avoids import cycle
    from app.blueprints.transfers import run_due_transfers
    run_due_schedules(current_user.id)
    run_due_transfers(current_user.id)
    selected_month = request.args.get('month')
    months = []
    today = datetime.today()
    for i in range(12):
        month = today.month - i
        year = today.year
        if month <= 0:
            month += 12
            year -= 1
        months.append(f'{year}-{month:02d}')
    filter_year = None
    filter_month = None
    if selected_month:
        filter_year, filter_month = selected_month.split('-')

    conn = get_db_connection()
    cursor = conn.cursor()

    if selected_month:
        cursor.execute("""
            SELECT c.name, SUM(t.amount) AS total
            FROM transactions t
            JOIN categories c ON t.category_id = c.id
            WHERE t.user_id = %s AND t.transaction_type = 'expense' AND t.is_transfer = false AND t.is_adjustment = false
            AND EXTRACT(YEAR FROM t.transaction_date) = %s
            AND EXTRACT(MONTH FROM t.transaction_date) = %s
            GROUP BY c.name
            ORDER BY total DESC
        """, (current_user.id, filter_year, filter_month))
    else:
        cursor.execute("""
            SELECT c.name, SUM(t.amount) AS total
            FROM transactions t
            JOIN categories c ON t.category_id = c.id
            WHERE t.user_id = %s AND t.transaction_type = 'expense' AND t.is_transfer = false AND t.is_adjustment = false
            GROUP BY c.name
            ORDER BY total DESC
        """, (current_user.id,))
    spending = cursor.fetchall()

    if selected_month:
        cursor.execute("""
            SELECT
                TO_CHAR(DATE_TRUNC('month', transaction_date), 'YYYY-MM') AS month,
                SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE 0 END) AS income,
                SUM(CASE WHEN transaction_type = 'expense' THEN amount ELSE 0 END) AS expenses
            FROM transactions
            WHERE user_id = %s AND is_transfer = false AND is_adjustment = false
            AND EXTRACT(YEAR FROM transaction_date) = %s
            AND EXTRACT(MONTH FROM transaction_date) = %s
            GROUP BY DATE_TRUNC('month', transaction_date)
            ORDER BY DATE_TRUNC('month', transaction_date)
        """, (current_user.id, filter_year, filter_month))
    else:
        cursor.execute("""
            SELECT
                TO_CHAR(DATE_TRUNC('month', transaction_date), 'YYYY-MM') AS month,
                SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE 0 END) AS income,
                SUM(CASE WHEN transaction_type = 'expense' THEN amount ELSE 0 END) AS expenses
            FROM transactions
            WHERE user_id = %s AND is_transfer = false AND is_adjustment = false
            GROUP BY DATE_TRUNC('month', transaction_date)
            ORDER BY DATE_TRUNC('month', transaction_date)
        """, (current_user.id,))
    cash_flow = cursor.fetchall()

    cursor.execute("""
        SELECT
            TO_CHAR(DATE_TRUNC('month', transaction_date), 'YYYY-MM') AS month,
            SUM(SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE -amount END))
            OVER (ORDER BY DATE_TRUNC('month', transaction_date)) AS running_balance
        FROM transactions
        WHERE user_id = %s
        GROUP BY DATE_TRUNC('month', transaction_date)
        ORDER BY DATE_TRUNC('month', transaction_date)
    """, (current_user.id,))
    net_balance_trend = cursor.fetchall()

    cursor.execute("""
        SELECT
            a.account_name,
            COALESCE(SUM(CASE WHEN t.transaction_type = 'income' THEN t.amount ELSE -t.amount END), 0) AS balance
        FROM account a
        LEFT JOIN transactions t ON a.account_id = t.account_id AND t.user_id = a.user_id
        WHERE a.user_id = %s
        GROUP BY a.account_id, a.account_name
        ORDER BY balance DESC
    """, (current_user.id,))
    account_balances = cursor.fetchall()

    # Monthly budget vs this-month (or selected-month) actual — same helper the
    # analytics page uses. Returns (category, budget, actual, remaining).
    budget_data = compute_budget_vs_actual(current_user.id, filter_year, filter_month)

    goals_view = build_goals_view(cursor, current_user.id)

    # AI cards (both independent of the chart month filter so their cache keys
    # stay stable; cache-only on load, no model call). Each is positioned at a
    # distinct moment in time and HIDDEN entirely when its target month has
    # nothing to say (v10.6) — so the dashboard never leads with a dead card:
    #   * Insight (v10.1) is RETROSPECTIVE → the last COMPLETE month, so on the
    #     1st of a new month it shows a fully-populated prior-month recap rather
    #     than an empty in-progress one. Shown only if that month had activity.
    #   * Forecast (v10.2) is PROSPECTIVE → the current month. Shown only when
    #     there's something to project (month-to-date activity OR a scheduled
    #     item still to land) — mirrors the generator's own not-enough-data gate.
    ai_on = ai_enabled()
    insight = None
    insight_facts = None
    show_insight = False
    forecast = None
    forecast_facts = None
    show_forecast = False
    insight_year, insight_month = _prev_month(today.year, today.month)
    if ai_on:
        insight_facts = compute_month_facts(current_user.id, insight_year, insight_month)
        show_insight = insight_facts['income'] > 0 or insight_facts['expenses'] > 0
        if show_insight:
            insight = load_insight(cursor, current_user.id, insight_year, insight_month)

        forecast_facts = compute_forecast(current_user.id, today.year, today.month)
        show_forecast = not (forecast_facts['income_to_date'] == 0
                             and forecast_facts['expenses_to_date'] == 0
                             and not forecast_facts['remaining_items'])
        if show_forecast:
            forecast = load_forecast(cursor, current_user.id, today.year, today.month)

    cursor.close()
    conn.close()

    spending_json = json.dumps([{'category': r[0], 'total': float(r[1])} for r in spending])
    cash_flow_json = json.dumps([{'month': r[0], 'income': float(r[1]), 'expenses': float(r[2])} for r in cash_flow])
    net_balance_json = json.dumps([{'month': r[0], 'balance': float(r[1])} for r in net_balance_trend])
    account_json = json.dumps([{'account': r[0], 'balance': float(r[1])} for r in account_balances])
    budget_json = json.dumps([{'category': r[0], 'budget': float(r[1]), 'actual': float(r[2])} for r in budget_data])

    has_transactions = bool(cash_flow) or bool(spending)

    # v10.6 hero — income/expenses/net for the current view (a single selected
    # month, or all time). Derived from cash_flow (already fetched) — no extra query.
    hero_income = sum(float(r[1]) for r in cash_flow)
    hero_expenses = sum(float(r[2]) for r in cash_flow)
    summary = {
        'income': hero_income,
        'expenses': hero_expenses,
        'net': hero_income - hero_expenses,
        'savings_rate': ((hero_income - hero_expenses) / hero_income * 100) if hero_income > 0 else None,
        'label': selected_month if selected_month else 'All time',
    }

    return render_template('dashboard.html',
        summary=summary,
        spending_json=spending_json,
        cash_flow_json=cash_flow_json,
        net_balance_json=net_balance_json,
        account_json=account_json,
        budget_json=budget_json,
        months=months,
        selected_month=selected_month,
        has_transactions=has_transactions,
        goals=goals_view,
        ai_enabled=ai_on,
        show_insight=show_insight,
        insight=insight,
        facts=insight_facts,
        insight_year=insight_year,
        insight_month=insight_month,
        show_forecast=show_forecast,
        forecast=forecast,
        forecast_facts=forecast_facts,
        forecast_year=today.year,
        forecast_month=today.month
    )
