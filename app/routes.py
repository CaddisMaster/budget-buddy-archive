from app import app
from app.db import get_db_connection
from flask import render_template, request, redirect, url_for, flash
import json
from datetime import datetime, date

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
    conn = get_db_connection()
    cursor = conn.cursor()
    if selected_month:
        year, month = selected_month.split('-')
        cursor.execute("""
            SELECT t.id, t.amount, t.description, c.name, a.account_name, t.transaction_date, t.transaction_type
            FROM transactions t
            LEFT JOIN categories c ON t.category_id = c.id
            LEFT JOIN account a ON t.account_id = a.account_id
            WHERE EXTRACT(YEAR FROM t.transaction_date) = %s
            AND EXTRACT(MONTH FROM t.transaction_date) = %s
            ORDER BY t.transaction_date DESC
        """, (year, month))
    else:
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
    return render_template('history.html',
        transactions=all_transactions,
        months=months,
        selected_month=selected_month
    )

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
            WHERE t.transaction_type = 'expense'
            AND EXTRACT(YEAR FROM t.transaction_date) = %s
            AND EXTRACT(MONTH FROM t.transaction_date) = %s
            GROUP BY c.name
            ORDER BY total DESC
        """, (filter_year, filter_month))
    else:
        cursor.execute("""
            SELECT c.name, SUM(t.amount) AS total
            FROM transactions t
            JOIN categories c ON t.category_id = c.id
            WHERE t.transaction_type = 'expense'
            GROUP BY c.name
            ORDER BY total DESC
        """)
    spending_by_category = cursor.fetchall()

    if selected_month:
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0)
            FROM transactions
            WHERE transaction_type = 'income'
            AND EXTRACT(YEAR FROM transaction_date) = %s
            AND EXTRACT(MONTH FROM transaction_date) = %s
        """, (filter_year, filter_month))
    else:
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0)
            FROM transactions
            WHERE transaction_type = 'income'
        """)
    total_income = cursor.fetchone()[0]

    if selected_month:
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0)
            FROM transactions
            WHERE transaction_type = 'expense'
            AND EXTRACT(YEAR FROM transaction_date) = %s
            AND EXTRACT(MONTH FROM transaction_date) = %s
        """, (filter_year, filter_month))
    else:
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0)
            FROM transactions
            WHERE transaction_type = 'expense'
        """)
    total_expenses = cursor.fetchone()[0]

    if selected_month:
        cursor.execute("""
            SELECT COALESCE(SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE -amount END), 0)
            FROM transactions
            WHERE EXTRACT(YEAR FROM transaction_date) = %s
            AND EXTRACT(MONTH FROM transaction_date) = %s
        """, (filter_year, filter_month))
    else:
        cursor.execute("""
            SELECT COALESCE(SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE -amount END), 0)
            FROM transactions
        """)
    net_balance = cursor.fetchone()[0]

    if selected_month:
        cursor.execute("""
            SELECT
                TO_CHAR(DATE_TRUNC('month', transaction_date), 'YYYY-MM') AS month,
                SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE 0 END) AS income,
                SUM(CASE WHEN transaction_type = 'expense' THEN amount ELSE 0 END) AS expenses
            FROM transactions
            WHERE EXTRACT(YEAR FROM transaction_date) = %s
            AND EXTRACT(MONTH FROM transaction_date) = %s
            GROUP BY DATE_TRUNC('month', transaction_date)
            ORDER BY DATE_TRUNC('month', transaction_date)
        """, (filter_year, filter_month))
    else:
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
                AND t.transaction_date BETWEEN b.period_start AND b.period_end
            WHERE EXTRACT(YEAR FROM b.period_start) = %s
            AND EXTRACT(MONTH FROM b.period_start) = %s
            GROUP BY c.name, b.amount, b.period_start, b.period_end
            ORDER BY c.name
        """, (filter_year, filter_month))
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
        moving_avg_json=moving_avg_json,
        months=months,
        selected_month=selected_month
    )

@app.route('/budgets', methods=['GET', 'POST'])
def budgets():
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        category_id = request.form.get('category_id')
        amount = request.form.get('amount')
        period_start = request.form.get('period_start')
        period_end = request.form.get('period_end')
        try:
            cursor.execute(
                "INSERT INTO budgets (category_id, amount, period_start, period_end) VALUES (%s, %s, %s, %s)",
                (category_id, amount, period_start, period_end)
            )
            conn.commit()
            flash('Budget added successfully')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        return redirect(url_for('budgets'))
    cursor.execute("SELECT id, name FROM categories ORDER BY name")
    all_categories = cursor.fetchall()
    cursor.execute("""
        SELECT b.id, c.name, b.amount, b.period_start, b.period_end
        FROM budgets b
        JOIN categories c ON b.category_id = c.id
        ORDER BY b.period_start DESC
    """)
    all_budgets = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('budgets.html', categories=all_categories, budgets=all_budgets)


@app.route('/budgets/edit/<int:budget_id>', methods=['GET', 'POST'])
def edit_budget(budget_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        category_id = request.form.get('category_id')
        amount = request.form.get('amount')
        period_start = request.form.get('period_start')
        period_end = request.form.get('period_end')
        try:
            cursor.execute(
                "UPDATE budgets SET category_id=%s, amount=%s, period_start=%s, period_end=%s WHERE id=%s",
                (category_id, amount, period_start, period_end, budget_id)
            )
            conn.commit()
            flash('Budget updated successfully')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect(url_for('budgets'))
    cursor.execute("SELECT id, category_id, amount, period_start, period_end FROM budgets WHERE id = %s", (budget_id,))
    budget = cursor.fetchone()
    cursor.execute("SELECT id, name FROM categories ORDER BY name")
    all_categories = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('edit_budget.html', budget=budget, categories=all_categories)

@app.route('/budgets/delete/<int:budget_id>', methods=['GET', 'POST'])
def delete_budget(budget_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        try:
            cursor.execute("DELETE FROM budgets WHERE id = %s", (budget_id,))
            conn.commit()
            flash('Budget deleted')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect(url_for('budgets'))
    cursor.execute("""
        SELECT b.id, c.name, b.amount, b.period_start, b.period_end
        FROM budgets b JOIN categories c ON b.category_id = c.id
        WHERE b.id = %s
    """, (budget_id,))
    budget = cursor.fetchone()
    cursor.close()
    conn.close()
    return render_template('delete_budget.html', budget=budget)

@app.route('/transactions/edit/<int:transaction_id>', methods=['GET', 'POST'])
def edit_transaction(transaction_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        amount_str = request.form['amount'].strip()
        description = request.form['description'].strip()
        transaction_date = request.form['transaction_date'].strip()
        category_id = request.form.get('category_id') or None
        account_id = request.form.get('account_id') or None
        transaction_type = request.form.get('transaction_type', 'expense')
        try:
            amount = float(amount_str)
            cursor.execute(
                "UPDATE transactions SET amount=%s, description=%s, transaction_date=%s, category_id=%s, account_id=%s, transaction_type=%s WHERE id=%s",
                (amount, description, transaction_date, category_id, account_id, transaction_type, transaction_id)
            )
            conn.commit()
            flash('Transaction updated successfully')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect(url_for('transactions'))
    cursor.execute("SELECT id, amount, description, transaction_date, category_id, account_id, transaction_type FROM transactions WHERE id = %s", (transaction_id,))
    transaction = cursor.fetchone()
    cursor.execute("SELECT id, name FROM categories ORDER BY name")
    all_categories = cursor.fetchall()
    cursor.execute("SELECT account_id, account_name FROM account ORDER BY account_name")
    all_accounts = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('edit_transaction.html', transaction=transaction, categories=all_categories, accounts=all_accounts)


@app.route('/transactions/delete/<int:transaction_id>', methods=['GET', 'POST'])
def delete_transaction(transaction_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        try:
            cursor.execute("DELETE FROM transactions WHERE id = %s", (transaction_id,))
            conn.commit()
            flash('Transaction deleted')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect(url_for('transactions'))
    cursor.execute("""
        SELECT t.id, t.amount, t.description, t.transaction_date, t.transaction_type
        FROM transactions t WHERE t.id = %s
    """, (transaction_id,))
    transaction = cursor.fetchone()
    cursor.close()
    conn.close()
    return render_template('delete_transaction.html', transaction=transaction)


@app.route('/categories/edit/<int:category_id>', methods=['GET', 'POST'])
def edit_category(category_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        name = request.form['name'].strip()
        description = request.form.get('description', '').strip()
        try:
            cursor.execute(
                "UPDATE categories SET name=%s, description=%s WHERE id=%s",
                (name, description, category_id)
            )
            conn.commit()
            flash('Category updated successfully')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect(url_for('categories'))
    cursor.execute("SELECT id, name, description FROM categories WHERE id = %s", (category_id,))
    category = cursor.fetchone()
    cursor.close()
    conn.close()
    return render_template('edit_category.html', category=category)


@app.route('/categories/delete/<int:category_id>', methods=['GET', 'POST'])
def delete_category(category_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        try:
            cursor.execute("DELETE FROM categories WHERE id = %s", (category_id,))
            conn.commit()
            flash('Category deleted')
        except Exception as e:
            if 'foreign key' in str(e).lower():
                flash('Cannot delete — this category is used by existing transactions or budgets')
            else:
                flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect(url_for('categories'))
    cursor.execute("SELECT id, name, description FROM categories WHERE id = %s", (category_id,))
    category = cursor.fetchone()
    cursor.close()
    conn.close()
    return render_template('delete_category.html', category=category)


@app.route('/accounts/edit/<int:account_id>', methods=['GET', 'POST'])
def edit_account(account_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        name = request.form['name'].strip()
        account_type = request.form.get('type', '').strip()
        try:
            cursor.execute(
                "UPDATE account SET account_name=%s, type=%s WHERE account_id=%s",
                (name, account_type, account_id)
            )
            conn.commit()
            flash('Account updated successfully')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect(url_for('accounts'))
    cursor.execute("SELECT account_id, account_name, type FROM account WHERE account_id = %s", (account_id,))
    account = cursor.fetchone()
    cursor.close()
    conn.close()
    return render_template('edit_account.html', account=account)


@app.route('/accounts/delete/<int:account_id>', methods=['GET', 'POST'])
def delete_account(account_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        try:
            cursor.execute("DELETE FROM account WHERE account_id = %s", (account_id,))
            conn.commit()
            flash('Account deleted')
        except Exception as e:
            if 'foreign key' in str(e).lower():
                flash('Cannot delete — this account is used by existing transactions')
            else:
                flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect(url_for('accounts'))
    cursor.execute("SELECT account_id, account_name, type FROM account WHERE account_id = %s", (account_id,))
    account = cursor.fetchone()
    cursor.close()
    conn.close()
    return render_template('delete_account.html', account=account)

@app.route('/dashboard')
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
            WHERE t.transaction_type = 'expense'
            AND EXTRACT(YEAR FROM t.transaction_date) = %s
            AND EXTRACT(MONTH FROM t.transaction_date) = %s
            GROUP BY c.name
            ORDER BY total DESC
        """, (filter_year, filter_month))
    else:
        cursor.execute("""
            SELECT c.name, SUM(t.amount) AS total
            FROM transactions t
            JOIN categories c ON t.category_id = c.id
            WHERE t.transaction_type = 'expense'
            GROUP BY c.name
            ORDER BY total DESC
        """)
    spending = cursor.fetchall()

    if selected_month:
        cursor.execute("""
            SELECT
                TO_CHAR(DATE_TRUNC('month', transaction_date), 'YYYY-MM') AS month,
                SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE 0 END) AS income,
                SUM(CASE WHEN transaction_type = 'expense' THEN amount ELSE 0 END) AS expenses
            FROM transactions
            WHERE EXTRACT(YEAR FROM transaction_date) = %s
            AND EXTRACT(MONTH FROM transaction_date) = %s
            GROUP BY DATE_TRUNC('month', transaction_date)
            ORDER BY DATE_TRUNC('month', transaction_date)
        """, (filter_year, filter_month))
    else:
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
            TO_CHAR(DATE_TRUNC('month', transaction_date), 'YYYY-MM') AS month,
            SUM(SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE -amount END))
            OVER (ORDER BY DATE_TRUNC('month', transaction_date)) AS running_balance
        FROM transactions
        GROUP BY DATE_TRUNC('month', transaction_date)
        ORDER BY DATE_TRUNC('month', transaction_date)
    """)
    net_balance_trend = cursor.fetchall()

    cursor.execute("""
        SELECT
            a.account_name,
            COALESCE(SUM(CASE WHEN t.transaction_type = 'income' THEN t.amount ELSE -t.amount END), 0) AS balance
        FROM account a
        LEFT JOIN transactions t ON a.account_id = t.account_id
        GROUP BY a.account_id, a.account_name
        ORDER BY balance DESC
    """)
    account_balances = cursor.fetchall()

    if selected_month:
        cursor.execute("""
            SELECT
                c.name AS category,
                b.amount AS budget,
                COALESCE(SUM(t.amount), 0) AS actual
            FROM budgets b
            JOIN categories c ON b.category_id = c.id
            LEFT JOIN transactions t ON t.category_id = b.category_id
                AND t.transaction_type = 'expense'
                AND t.transaction_date BETWEEN b.period_start AND b.period_end
            WHERE EXTRACT(YEAR FROM b.period_start) = %s
            AND EXTRACT(MONTH FROM b.period_start) = %s
            GROUP BY c.name, b.amount, b.period_start, b.period_end
            ORDER BY c.name
        """, (filter_year, filter_month))
    else:
        cursor.execute("""
            SELECT
                c.name AS category,
                b.amount AS budget,
                COALESCE(SUM(t.amount), 0) AS actual
            FROM budgets b
            JOIN categories c ON b.category_id = c.id
            LEFT JOIN transactions t ON t.category_id = b.category_id
                AND t.transaction_type = 'expense'
                AND t.transaction_date BETWEEN b.period_start AND b.period_end
            GROUP BY c.name, b.amount, b.period_start, b.period_end
            ORDER BY c.name
        """)
    budget_data = cursor.fetchall()

    cursor.close()
    conn.close()

    spending_json = json.dumps([{'category': r[0], 'total': float(r[1])} for r in spending])
    cash_flow_json = json.dumps([{'month': r[0], 'income': float(r[1]), 'expenses': float(r[2])} for r in cash_flow])
    net_balance_json = json.dumps([{'month': r[0], 'balance': float(r[1])} for r in net_balance_trend])
    account_json = json.dumps([{'account': r[0], 'balance': float(r[1])} for r in account_balances])
    budget_json = json.dumps([{'category': r[0], 'budget': float(r[1]), 'actual': float(r[2])} for r in budget_data])

    return render_template('dashboard.html',
        spending_json=spending_json,
        cash_flow_json=cash_flow_json,
        net_balance_json=net_balance_json,
        account_json=account_json,
        budget_json=budget_json,
        months=months,
        selected_month=selected_month
    )