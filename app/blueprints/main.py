import calendar
from datetime import date, datetime
from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from app.db import get_db_connection, db_cursor
from app.helpers import ai_enabled, recent_months
from app.blueprints.goals import build_goals_view
from app.blueprints.budgets import compute_budget_vs_actual
from app.blueprints.insights import load_insight, compute_month_facts, _prev_month
from app.blueprints.forecasts import load_forecast, compute_forecast
from app.blueprints.agent import load_agent_run
from app.blueprints.transactions import compute_next_due

bp = Blueprint('main', __name__)

# Account types whose balance is spendable cash (a subset of accounts.py
# VALID_ACCOUNT_TYPES). Credit Card is excluded — its balance is debt, and
# paying it down shows up as a scheduled transfer out of a liquid account.
LIQUID_ACCOUNT_TYPES = ('Bank Account', 'Debit Card')


def _advance_past(occ, frequency, anchor_day, second_day, day, guard=500):
    """First occurrence strictly after `day`, walking from `occ` with
    compute_next_due. Pure. The guard caps runaway walks on bad data."""
    steps = 0
    while occ <= day and steps < guard:
        occ = compute_next_due(occ, frequency, anchor_day=anchor_day,
                               second_day=second_day)
        steps += 1
    return occ


def upcoming_occurrences(next_due, frequency, anchor_day, second_day,
                         window_start, window_end, guard=500):
    """All occurrence dates with window_start < occ <= window_end. Pure.

    Start is exclusive (anything due today was already materialized by the
    due-runners); end is INCLUSIVE — a bill due on payday still has to be paid
    out of this check. Each phase gets its own guard budget so a long catch-up
    can't starve the collection loop (the forecasts._remaining_scheduled
    precedent)."""
    occ = _advance_past(next_due, frequency, anchor_day, second_day,
                        window_start, guard)
    out = []
    steps = 0
    while occ <= window_end and steps < guard:
        out.append(occ)
        occ = compute_next_due(occ, frequency, anchor_day=anchor_day,
                               second_day=second_day)
        steps += 1
    return out


def compute_safe_to_spend(user_id, today=None):
    """Deterministic "can I afford this right now?" figure — always for NOW,
    independent of the dashboard month filter (the AI-card precedent below).

        safe_to_spend = liquid_balance − upcoming_bills
                        − upcoming_transfers_out − budget_room

    liquid_balance sums the canonical balances of Bank/Debit accounts marked
    spendable (v10.10: the per-account toggle EXCLUDES a savings-style account
    from the pool; it can't pull a credit card IN — liquid stays type-gated).
    The window runs from today (exclusive) through the next paycheck date
    INCLUSIVE — the soonest upcoming occurrence across active income
    schedules — falling back to the last day of the current month when no
    income schedule exists. Bills count on liquid accounts, plus (v10.10) on
    credit cards with NO active transfer schedule paying that card — a
    card-billed bill still has to be paid in cash, unless the scheduled
    payment transfer already reserves it. Bills count only in UNbudgeted
    categories — budgeted categories are covered by budget_room, so each
    dollar is reserved once. Transfers count only liquid → non-liquid
    (liquid → liquid nets to zero inside liquid_balance; a transfer INTO a
    non-spendable account is money leaving the pool, so it counts).
    budget_room is max(0, budget − spent) summed over this calendar month's
    budgets — a full-month term by design, budgets being monthly commitments.
    The result may be negative; that's the point.
    """
    today = today or date.today()

    with db_cursor() as cursor:
        # Liquid balances — the ACCOUNT_ROW_SQL balance formula (accounts.py),
        # transfers/adjustments included, filtered to liquid types.
        cursor.execute("""
            SELECT a.account_id,
                COALESCE(SUM(CASE WHEN t.transaction_type = 'income'
                    THEN t.amount ELSE -t.amount END), 0) AS balance
            FROM account a
            LEFT JOIN transactions t ON a.account_id = t.account_id AND t.user_id = a.user_id
            WHERE a.user_id = %s AND a.type IN %s AND a.spendable = true
            GROUP BY a.account_id
        """, (user_id, LIQUID_ACCOUNT_TYPES))
        rows = cursor.fetchall()
        liquid_ids = {r[0] for r in rows}
        liquid_balance = round(sum(float(r[1]) for r in rows), 2)

        cursor.execute("""
            SELECT frequency, anchor_day, second_day, next_due
            FROM schedules
            WHERE is_active = true AND user_id = %s AND transaction_type = 'income'
        """, (user_id,))
        income_rows = cursor.fetchall()
        if income_rows:
            window_end = min(_advance_past(nd, freq, anchor, second, today)
                             for freq, anchor, second, nd in income_rows)
            window_is_paycheck = True
        else:
            window_end = date(today.year, today.month,
                              calendar.monthrange(today.year, today.month)[1])
            window_is_paycheck = False

        cursor.execute("SELECT category_id FROM budgets WHERE user_id = %s",
                       (user_id,))
        budgeted_ids = {r[0] for r in cursor.fetchall()}

        cursor.execute("""
            SELECT account_id FROM account
            WHERE user_id = %s AND type = 'Credit Card'
        """, (user_id,))
        credit_ids = {r[0] for r in cursor.fetchall()}

        # Cards already covered by a scheduled payment transfer — their bills
        # must NOT also count, or the payment would be reserved twice.
        cursor.execute("""
            SELECT DISTINCT to_account_id FROM transfer_schedules
            WHERE user_id = %s AND is_active = true
        """, (user_id,))
        paid_card_ids = {r[0] for r in cursor.fetchall()}

        cursor.execute("""
            SELECT description, amount, frequency, anchor_day, second_day,
                   next_due, account_id, category_id
            FROM schedules
            WHERE is_active = true AND user_id = %s AND transaction_type = 'expense'
        """, (user_id,))
        bill_items = []
        for desc, amount, freq, anchor, second, nd, account_id, category_id in cursor.fetchall():
            drains_cash = (account_id in liquid_ids
                           or (account_id in credit_ids
                               and account_id not in paid_card_ids))
            if not drains_cash or category_id in budgeted_ids:
                continue
            for due in upcoming_occurrences(nd, freq, anchor, second,
                                            today, window_end):
                bill_items.append({
                    'description': (desc or '').strip() or 'Scheduled bill',
                    'amount': float(amount), 'due': due,
                })
        bill_items.sort(key=lambda i: i['due'])

        cursor.execute("""
            SELECT ts.description, ts.amount, ts.frequency, ts.anchor_day,
                   ts.second_day, ts.next_due, ts.from_account_id,
                   ts.to_account_id, af.account_name, at.account_name
            FROM transfer_schedules ts
            JOIN account af ON ts.from_account_id = af.account_id
            JOIN account at ON ts.to_account_id = at.account_id
            WHERE ts.is_active = true AND ts.user_id = %s
        """, (user_id,))
        transfer_items = []
        for (desc, amount, freq, anchor, second, nd, from_id, to_id,
             from_name, to_name) in cursor.fetchall():
            if from_id not in liquid_ids or to_id in liquid_ids:
                continue
            label = (desc or '').strip() or f'Transfer {from_name} → {to_name}'
            for due in upcoming_occurrences(nd, freq, anchor, second,
                                            today, window_end):
                transfer_items.append({
                    'description': label, 'amount': float(amount), 'due': due,
                })
        transfer_items.sort(key=lambda i: i['due'])

    # Opens its own connection, so it sits outside the cursor block.
    budget_rows = compute_budget_vs_actual(user_id, today.year, today.month)
    budget_room = round(sum(max(0.0, float(remaining))
                            for _, _, _, remaining in budget_rows), 2)

    upcoming_bills = round(sum(i['amount'] for i in bill_items), 2)
    upcoming_transfers_out = round(sum(i['amount'] for i in transfer_items), 2)

    return {
        'liquid_balance': liquid_balance,
        'upcoming_bills': upcoming_bills,
        'upcoming_transfers_out': upcoming_transfers_out,
        'budget_room': budget_room,
        'safe_to_spend': round(liquid_balance - upcoming_bills
                               - upcoming_transfers_out - budget_room, 2),
        'window_end': window_end,
        'window_is_paycheck': window_is_paycheck,
        'bill_items': bill_items,
        'transfer_items': transfer_items,
    }


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
    months = recent_months()
    today = datetime.today()
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

    # Spending by day of week (merged from /analytics, v10.9) — expense totals
    # grouped Sunday-first by Postgres DOW.
    if selected_month:
        cursor.execute("""
            SELECT
                EXTRACT(DOW FROM transaction_date) AS dow,
                TO_CHAR(transaction_date, 'Day') AS day_name,
                SUM(amount) AS total
            FROM transactions
            WHERE user_id = %s AND transaction_type = 'expense' AND is_adjustment = false AND is_transfer = false
            AND EXTRACT(YEAR FROM transaction_date) = %s
            AND EXTRACT(MONTH FROM transaction_date) = %s
            GROUP BY dow, day_name
            ORDER BY dow
        """, (current_user.id, filter_year, filter_month))
    else:
        cursor.execute("""
            SELECT
                EXTRACT(DOW FROM transaction_date) AS dow,
                TO_CHAR(transaction_date, 'Day') AS day_name,
                SUM(amount) AS total
            FROM transactions
            WHERE user_id = %s AND transaction_type = 'expense' AND is_adjustment = false AND is_transfer = false
            GROUP BY dow, day_name
            ORDER BY dow
        """, (current_user.id,))
    spending_by_day = cursor.fetchall()

    # Year over year (merged from /analytics, v10.9) — expenses for the same
    # month last year. Only meaningful when a single month is selected; the
    # this-year side reuses the hero's expense total (same filters), computed
    # after the cursor closes.
    last_year_expenses = None
    if selected_month:
        cursor.execute("""
            SELECT SUM(CASE WHEN transaction_type='expense' THEN amount ELSE 0 END)
            FROM transactions
            WHERE user_id = %s AND is_adjustment = false AND is_transfer = false
            AND EXTRACT(YEAR FROM transaction_date) = %s
            AND EXTRACT(MONTH FROM transaction_date) = %s
        """, (current_user.id, int(filter_year) - 1, filter_month))
        last_year_expenses = float(cursor.fetchone()[0] or 0)

    # Monthly budget vs this-month (or selected-month) actual.
    # Returns (category, budget, actual, remaining).
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
    agent_run = None
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

        # Money agent (v10.10) — the latest cached weekly run. Unlike the two
        # cards above there's no not-enough-data gate: the empty state IS the
        # card (it carries the Run-now button), so it shows whenever AI is on.
        agent_run = load_agent_run(cursor, current_user.id)

    cursor.close()
    conn.close()

    # Chart payloads — plain lists the template renders with |tojson, which
    # HTML-escapes into the script block (the old json.dumps + |safe let a
    # </script> in a category/account name break out of it).
    spending_data = [{'category': r[0], 'total': float(r[1])} for r in spending]
    cash_flow_data = [{'month': r[0], 'income': float(r[1]), 'expenses': float(r[2])} for r in cash_flow]
    net_balance_data = [{'month': r[0], 'balance': float(r[1])} for r in net_balance_trend]
    account_data = [{'account': r[0], 'balance': float(r[1])} for r in account_balances]
    budget_chart_data = [{'category': r[0], 'budget': float(r[1]), 'actual': float(r[2])} for r in budget_data]
    day_of_week_data = [{'day': r[1].strip(), 'total': float(r[2])} for r in spending_by_day]

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

    # Year over year — only when a month is selected AND last year has data;
    # hero_expenses is the same-filtered this-year total.
    yoy = None
    if last_year_expenses:
        yoy = {
            'last_year': last_year_expenses,
            'this_year': hero_expenses,
            'change': round((hero_expenses - last_year_expenses)
                            / last_year_expenses * 100, 1),
        }

    # Safe to spend (v10.9) — always "now", independent of the month filter
    # (like the AI cards). The due-runners at the top of this route already
    # advanced next_due past today.
    safe = compute_safe_to_spend(current_user.id)

    return render_template('dashboard.html',
        summary=summary,
        safe=safe,
        yoy=yoy,
        spending_data=spending_data,
        cash_flow_data=cash_flow_data,
        net_balance_data=net_balance_data,
        account_data=account_data,
        budget_chart_data=budget_chart_data,
        day_of_week_data=day_of_week_data,
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
        forecast_month=today.month,
        agent_run=agent_run
    )
