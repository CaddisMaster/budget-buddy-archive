import json
from datetime import datetime
from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from app.db import get_db_connection
from app.helpers import ai_enabled
from app.blueprints.goals import build_goals_view
from app.blueprints.budgets import compute_budget_vs_actual

bp = Blueprint('main', __name__)


@bp.route('/')
@login_required
def index():
    return render_template('index.html', ai_enabled=ai_enabled())


@bp.route('/dashboard')
@login_required
def dashboard():
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
            WHERE t.user_id = %s AND t.transaction_type = 'expense' AND t.is_transfer = false
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
            WHERE t.user_id = %s AND t.transaction_type = 'expense' AND t.is_transfer = false
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
            WHERE user_id = %s AND is_transfer = false
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
            WHERE user_id = %s AND is_transfer = false
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

    cursor.close()
    conn.close()

    spending_json = json.dumps([{'category': r[0], 'total': float(r[1])} for r in spending])
    cash_flow_json = json.dumps([{'month': r[0], 'income': float(r[1]), 'expenses': float(r[2])} for r in cash_flow])
    net_balance_json = json.dumps([{'month': r[0], 'balance': float(r[1])} for r in net_balance_trend])
    account_json = json.dumps([{'account': r[0], 'balance': float(r[1])} for r in account_balances])
    budget_json = json.dumps([{'category': r[0], 'budget': float(r[1]), 'actual': float(r[2])} for r in budget_data])

    has_transactions = bool(cash_flow) or bool(spending)
    return render_template('dashboard.html',
        spending_json=spending_json,
        cash_flow_json=cash_flow_json,
        net_balance_json=net_balance_json,
        account_json=account_json,
        budget_json=budget_json,
        months=months,
        selected_month=selected_month,
        has_transactions=has_transactions,
        goals=goals_view
    )
