import json
from datetime import datetime
from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from app.db import get_db_connection

bp = Blueprint('analytics', __name__)


@bp.route('/analytics')
@login_required
def analytics():
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
            WHERE t.user_id = %s AND t.transaction_type = 'expense' AND t.is_adjustment = false
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
            WHERE t.user_id = %s AND t.transaction_type = 'expense' AND t.is_adjustment = false
            GROUP BY c.name
            ORDER BY total DESC
        """, (current_user.id,))
    spending_by_category = cursor.fetchall()

    if selected_month:
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0)
            FROM transactions
            WHERE user_id = %s AND transaction_type = 'income' AND is_adjustment = false
            AND EXTRACT(YEAR FROM transaction_date) = %s
            AND EXTRACT(MONTH FROM transaction_date) = %s
        """, (current_user.id, filter_year, filter_month))
    else:
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0)
            FROM transactions
            WHERE user_id = %s AND transaction_type = 'income' AND is_adjustment = false
        """, (current_user.id,))
    total_income = cursor.fetchone()[0]

    if selected_month:
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0)
            FROM transactions
            WHERE user_id = %s AND transaction_type = 'expense' AND is_adjustment = false
            AND EXTRACT(YEAR FROM transaction_date) = %s
            AND EXTRACT(MONTH FROM transaction_date) = %s
        """, (current_user.id, filter_year, filter_month))
    else:
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0)
            FROM transactions
            WHERE user_id = %s AND transaction_type = 'expense' AND is_adjustment = false
        """, (current_user.id,))
    total_expenses = cursor.fetchone()[0]

    if selected_month:
        cursor.execute("""
            SELECT COALESCE(SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE -amount END), 0)
            FROM transactions
            WHERE user_id = %s AND is_adjustment = false
            AND EXTRACT(YEAR FROM transaction_date) = %s
            AND EXTRACT(MONTH FROM transaction_date) = %s
        """, (current_user.id, filter_year, filter_month))
    else:
        cursor.execute("""
            SELECT COALESCE(SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE -amount END), 0)
            FROM transactions
            WHERE user_id = %s AND is_adjustment = false
        """, (current_user.id,))
    net_balance = cursor.fetchone()[0]

    if selected_month:
        cursor.execute("""
            SELECT
                TO_CHAR(DATE_TRUNC('month', transaction_date), 'YYYY-MM') AS month,
                SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE 0 END) AS income,
                SUM(CASE WHEN transaction_type = 'expense' THEN amount ELSE 0 END) AS expenses
            FROM transactions
            WHERE user_id = %s AND is_adjustment = false
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
            WHERE user_id = %s AND is_adjustment = false
            GROUP BY DATE_TRUNC('month', transaction_date)
            ORDER BY DATE_TRUNC('month', transaction_date)
        """, (current_user.id,))
    cash_flow = cursor.fetchall()

    if selected_month:
        cursor.execute("""
            SELECT
                c.name AS category,
                b.amount AS budget,
                COALESCE(SUM(t.amount), 0) AS actual,
                b.amount - COALESCE(SUM(t.amount), 0) AS remaining
            FROM budgets b
            JOIN categories c ON b.category_id = c.id
            LEFT JOIN transactions t ON t.category_id = b.category_id
                AND t.transaction_type = 'expense'
                AND t.is_adjustment = false
                AND t.transaction_date BETWEEN b.period_start AND b.period_end
                AND t.user_id = b.user_id
            WHERE b.user_id = %s
            AND EXTRACT(YEAR FROM b.period_start) = %s
            AND EXTRACT(MONTH FROM b.period_start) = %s
            GROUP BY c.name, b.amount, b.period_start, b.period_end
            ORDER BY c.name
        """, (current_user.id, filter_year, filter_month))
    else:
        cursor.execute("""
            SELECT
                c.name AS category,
                b.amount AS budget,
                COALESCE(SUM(t.amount), 0) AS actual,
                b.amount - COALESCE(SUM(t.amount), 0) AS remaining
            FROM budgets b
            JOIN categories c ON b.category_id = c.id
            LEFT JOIN transactions t ON t.category_id = b.category_id
                AND t.transaction_type = 'expense'
                AND t.is_adjustment = false
                AND t.transaction_date BETWEEN b.period_start AND b.period_end
                AND t.user_id = b.user_id
            WHERE b.user_id = %s
            GROUP BY c.name, b.amount, b.period_start, b.period_end
            ORDER BY c.name
        """, (current_user.id,))
    budget_vs_actual = cursor.fetchall()

    cursor.execute("""
        WITH weekly_totals AS (
            SELECT
                DATE_TRUNC('week', transaction_date) AS week,
                SUM(amount) AS weekly_total
            FROM transactions
            WHERE user_id = %s AND transaction_type = 'expense' AND is_adjustment = false
            GROUP BY DATE_TRUNC('week', transaction_date)
        )
        SELECT
            TO_CHAR(week, 'YYYY-MM-DD') AS week_label,
            weekly_total,
            AVG(weekly_total) OVER (
                ORDER BY week
                ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
            ) AS moving_avg
        FROM weekly_totals
        ORDER BY week
    """, (current_user.id,))
    moving_averages = cursor.fetchall()

    if selected_month:
        cursor.execute("""
            SELECT
                SUM(CASE WHEN transaction_type='income' THEN amount ELSE 0 END) AS income,
                SUM(CASE WHEN transaction_type='expense' THEN amount ELSE 0 END) AS expenses
            FROM transactions
            WHERE user_id = %s AND is_adjustment = false
            AND EXTRACT(YEAR FROM transaction_date) = %s
            AND EXTRACT(MONTH FROM transaction_date) = %s
        """, (current_user.id, filter_year, filter_month))
    else:
        cursor.execute("""
            SELECT
                SUM(CASE WHEN transaction_type='income' THEN amount ELSE 0 END) AS income,
                SUM(CASE WHEN transaction_type='expense' THEN amount ELSE 0 END) AS expenses
            FROM transactions
            WHERE user_id = %s AND is_adjustment = false
        """, (current_user.id,))
    row = cursor.fetchone()
    s_income, s_expenses = float(row[0] or 0), float(row[1] or 0)
    if s_income > 0:
        savings_rate = round((s_income - s_expenses) / s_income * 100, 1)
    else:
        savings_rate = None

    yoy = None
    if selected_month:
        y, m = selected_month.split('-')
        cursor.execute("""
            SELECT SUM(CASE WHEN transaction_type='expense' THEN amount ELSE 0 END)
            FROM transactions
            WHERE user_id = %s AND is_adjustment = false
            AND EXTRACT(YEAR FROM transaction_date) = %s
            AND EXTRACT(MONTH FROM transaction_date) = %s
        """, (current_user.id, int(y) - 1, m))
        last_year_row = cursor.fetchone()
        last_year_expenses = float(last_year_row[0] or 0)
        if last_year_expenses > 0:
            yoy = {
                'last_year': last_year_expenses,
                'this_year': float(total_expenses),
                'change': round(((float(total_expenses) - last_year_expenses) / last_year_expenses) * 100, 1)
            }

    if selected_month:
        cursor.execute("""
            SELECT
                EXTRACT(DOW FROM transaction_date) AS dow,
                TO_CHAR(transaction_date, 'Day') AS day_name,
                SUM(amount) AS total
            FROM transactions
            WHERE user_id = %s AND transaction_type = 'expense' AND is_adjustment = false
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
            WHERE user_id = %s AND transaction_type = 'expense' AND is_adjustment = false
            GROUP BY dow, day_name
            ORDER BY dow
        """, (current_user.id,))
    spending_by_day = cursor.fetchall()

    cursor.execute("""
        SELECT
            c.name AS category,
            ROUND(AVG(monthly_total)::numeric, 2) AS suggested_budget
        FROM (
            SELECT
                category_id,
                DATE_TRUNC('month', transaction_date) AS month,
                SUM(amount) AS monthly_total
            FROM transactions
            WHERE user_id = %s AND transaction_type = 'expense' AND is_adjustment = false
            AND transaction_date >= NOW() - INTERVAL '6 months'
            AND category_id IS NOT NULL
            GROUP BY category_id, DATE_TRUNC('month', transaction_date)
        ) monthly
        JOIN categories c ON c.id = monthly.category_id
        GROUP BY c.name
        HAVING COUNT(DISTINCT month) >= 1
        ORDER BY suggested_budget DESC
    """, (current_user.id,))
    budget_suggestions = cursor.fetchall()

    cash_flow_json = json.dumps([
        {'month': row[0], 'income': float(row[1]), 'expenses': float(row[2])}
        for row in cash_flow
    ])
    moving_avg_json = json.dumps([
        {'week': row[0], 'total': float(row[1]), 'avg': float(row[2])}
        for row in moving_averages
    ])
    cursor.close()
    conn.close()
    return render_template('analytics.html',
        spending_by_category=spending_by_category,
        total_income=total_income,
        total_expenses=total_expenses,
        net_balance=net_balance,
        cash_flow=cash_flow,
        budget_vs_actual=budget_vs_actual,
        moving_averages=moving_averages,
        cash_flow_json=cash_flow_json,
        moving_avg_json=moving_avg_json,
        months=months,
        selected_month=selected_month,
        savings_rate=savings_rate,
        yoy=yoy,
        spending_by_day=spending_by_day,
        budget_suggestions=budget_suggestions
    )
