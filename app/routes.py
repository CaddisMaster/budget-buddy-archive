from app import app
from app.db import get_db_connection
from flask import render_template, request, redirect, url_for, flash
import json

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/transactions/new', methods=['GET', 'POST'])
def new_transaction():
    if request.method == 'POST':
        amount_str = request.form['amount'].strip()
        description = request.form['description'].strip()
        transaction_date = request.form['transaction_date'].strip()
        category_id = request.form.get('category_id') or None
        account_id = request.form.get('account_id') or None
        transaction_type = request.form.get('transaction_type', 'expense')
        errors = []
        if not amount_str:
            errors.append('Amount is required')
        else:
            try:
                amount = float(amount_str)
                if amount <= 0:
                    errors.append('Amount must be greater than zero')
            except ValueError:
                errors.append('Amount must be a valid number')
        if not transaction_date:
            errors.append('Date is required')
        if not account_id:
            errors.append('Account is required')
        if transaction_type not in ('income', 'expense'):
            errors.append('Transaction type must be income or expense')
        if errors:
            for error in errors:
                flash(error)
            return redirect(url_for('new_transaction'))
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO transactions (amount, description, transaction_date, category_id, account_id, transaction_type) VALUES (%s, %s, %s, %s, %s, %s)",
                (amount, description, transaction_date, category_id, account_id, transaction_type)
            )
            conn.commit()
            flash('Transaction added successfully')
        except Exception as e:
            flash(f'Error: {e}')
            if conn:
                conn.rollback()
        finally:
            if cursor: cursor.close()
            if conn: conn.close()
        return redirect(url_for('new_transaction'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM categories ORDER BY name")
    all_categories = cursor.fetchall()
    cursor.execute("SELECT account_id, account_name FROM account ORDER BY account_name")
    all_accounts = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('new_transaction.html', categories=all_categories, accounts=all_accounts)

@app.route('/categories', methods=['GET', 'POST'])
def categories():
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        name = request.form['name'].strip()
        description = request.form.get('description', '').strip()
        try:
            cursor.execute(
                "INSERT INTO categories (name, description) VALUES (%s, %s)",
                (name, description)
            )
            conn.commit()
            flash('Category added successfully')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        return redirect(url_for('categories'))
    cursor.execute("SELECT id, name, description FROM categories ORDER BY name")
    all_categories = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('categories.html', categories=all_categories)

@app.route('/transactions')
def transactions():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT t.id, t.amount, t.description, c.name, a.account_name, t.transaction_date, t.transaction_type
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN account a ON t.account_id = a.account_id
        ORDER BY t.transaction_date DESC
    """)
    all_transactions = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('history.html', transactions=all_transactions)

@app.route('/accounts', methods=['GET', 'POST'])
def accounts():
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        name = request.form['name'].strip()
        account_type = request.form.get('type', '').strip()
        try:
            cursor.execute(
                "INSERT INTO account (account_name, type) VALUES (%s, %s)",
                (name, account_type)
            )
            conn.commit()
            flash('Account added successfully')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        return redirect(url_for('accounts'))
    cursor.execute("""
        SELECT
            a.account_id,
            a.account_name,
            a.type,
            COALESCE(SUM(
                CASE WHEN t.transaction_type = 'income'
                THEN t.amount
                ELSE -t.amount END
            ), 0) AS balance
        FROM account a
        LEFT JOIN transactions t ON a.account_id = t.account_id
        GROUP BY a.account_id, a.account_name, a.type
        ORDER BY a.account_name
    """)
    all_accounts = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('accounts.html', accounts=all_accounts)

@app.route('/analytics')
def analytics():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT c.name, SUM(t.amount) AS total
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        WHERE t.transaction_type = 'expense'
        GROUP BY c.name
        ORDER BY total DESC
    """)
    spending_by_category = cursor.fetchall()

    cursor.execute("""
        SELECT COALESCE(SUM(amount), 0)
        FROM transactions
        WHERE transaction_type = 'income'
    """)
    total_income = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COALESCE(SUM(amount), 0)
        FROM transactions
        WHERE transaction_type = 'expense'
    """)
    total_expenses = cursor.fetchone()[0]

    cursor.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE -amount END), 0) AS net_balance
        FROM transactions
    """)
    net_balance = cursor.fetchone()[0]

    cursor.execute("""
        SELECT
            TO_CHAR(DATE_TRUNC('month', transaction_date), 'YYYY-MM') AS month,
            SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE 0 END) AS income,
            SUM(CASE WHEN transaction_type = 'expense' THEN amount ELSE 0 END) AS expenses
        FROM transactions
        GROUP BY DATE_TRUNC('month', transaction_date)
        ORDER BY DATE_TRUNC('month', transaction_date)
    """)
    cash_flow = cursor.fetchall()

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
            AND t.transaction_date BETWEEN b.period_start AND b.period_end
        GROUP BY c.name, b.amount, b.period_start, b.period_end
        ORDER BY c.name
    """)
    budget_vs_actual = cursor.fetchall()

    cursor.execute("""
        WITH weekly_totals AS (
            SELECT
                DATE_TRUNC('week', transaction_date) AS week,
                SUM(amount) AS weekly_total
            FROM transactions
            WHERE transaction_type = 'expense'
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
    """)
    moving_averages = cursor.fetchall()

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
        moving_avg_json=moving_avg_json
    )