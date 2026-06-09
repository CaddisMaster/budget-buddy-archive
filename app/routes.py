from app import app
from app.db import get_db_connection
from flask import render_template, request, redirect, url_for, flash
import json
from datetime import datetime, date
import math
from dateutil.relativedelta import relativedelta
import csv
import io
from flask import make_response
import subprocess

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
        is_recurring = request.form.get('is_recurring') == 'true'
        frequency = request.form.get('frequency') if is_recurring else None
        next_due = None
        if is_recurring and frequency:
            next_due_raw = datetime.strptime(transaction_date, '%Y-%m-%d')
            from dateutil.relativedelta import relativedelta
            if frequency == 'monthly':
                next_due = (next_due_raw + relativedelta(months=1)).strftime('%Y-%m-%d')
            else:
                next_due = (next_due_raw + timedelta(weeks=1)).strftime('%Y-%m-%d')
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO transactions (amount, description, transaction_date, category_id, account_id, transaction_type, is_recurring, frequency, next_due) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (amount, description, transaction_date, category_id, account_id, transaction_type, is_recurring, frequency, next_due)
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

@app.route('/recurring/process')
def process_recurring():
    run_process_recurring()
    flash('Recurring transactions processed')
    return redirect('/transactions')

@app.route('/transactions')
def transactions():
    run_process_recurring()
    selected_month = request.args.get('month')
    search = request.args.get('search', '').strip()
    page = int(request.args.get('page', 1))
    per_page = 25
    offset = (page - 1) * per_page
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
    filters = []
    params = []
    if selected_month:
        year, month = selected_month.split('-')
        filters.append("EXTRACT(YEAR FROM t.transaction_date) = %s AND EXTRACT(MONTH FROM t.transaction_date) = %s")
        params.extend([year, month])
    if search:
        filters.append("t.description ILIKE %s")
        params.append(f'%{search}%')
    where_clause = "WHERE " + " AND ".join(filters) if filters else ""
    count_query = f"""
        SELECT COUNT(*) FROM transactions t {where_clause}
    """
    cursor.execute(count_query, params)
    total = cursor.fetchone()[0]
    total_pages = math.ceil(total / per_page) if total > 0 else 1
    main_query = f"""
        SELECT t.id, t.amount, t.description, c.name, a.account_name,
            t.transaction_date, t.transaction_type,
            t.is_recurring, t.frequency
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN account a ON t.account_id = a.account_id
        {where_clause}
        ORDER BY t.transaction_date DESC, t.id DESC
        LIMIT %s OFFSET %s
    """
    cursor.execute(main_query, params + [per_page, offset])
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    running_balance = 0
    transactions_with_balance = []
    for t in reversed(rows):
        if t[6] == 'income':
            running_balance += t[1]
        else:
            running_balance -= t[1]
        transactions_with_balance.append(t + (running_balance,))
    transactions_with_balance.reverse()
    return render_template('history.html',
        transactions=transactions_with_balance,
        months=months,
        selected_month=selected_month,
        search=search,
        page=page,
        total_pages=total_pages,
        total=total
    )

@app.route('/transactions/cancel-recurring/<int:id>', methods=['POST'])
def cancel_recurring(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE transactions SET is_recurring=false, frequency=NULL, next_due=NULL WHERE id=%s",
        (id,)
    )
    conn.commit()
    cursor.close()
    conn.close()
    flash('Recurring cancelled')
    return redirect('/transactions')

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

    if selected_month:
        cursor.execute("""
            SELECT
                SUM(CASE WHEN transaction_type='income' THEN amount ELSE 0 END) AS income,
                SUM(CASE WHEN transaction_type='expense' THEN amount ELSE 0 END) AS expenses
            FROM transactions
            WHERE EXTRACT(YEAR FROM transaction_date) = %s
            AND EXTRACT(MONTH FROM transaction_date) = %s
        """, (filter_year, filter_month))
    else:
        cursor.execute("""
            SELECT
                SUM(CASE WHEN transaction_type='income' THEN amount ELSE 0 END) AS income,
                SUM(CASE WHEN transaction_type='expense' THEN amount ELSE 0 END) AS expenses
            FROM transactions
        """)
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
            SELECT
            SUM(CASE WHEN transaction_type='expense' THEN amount ELSE 0 END)
            FROM transactions
            WHERE EXTRACT(YEAR FROM transaction_date) = %s
            AND EXTRACT(MONTH FROM transaction_date) = %s
        """, (int(y) - 1, m))
        last_year_row = cursor.fetchone()
        last_year_expenses = float(last_year_row[0] or 0)
        if last_year_expenses > 0:
            yoy = {
                'last_year': last_year_expenses,
                'this_year': float(total_expenses),
                'change': round(((float(total_expenses) - last_year_expenses) / last_year_expenses) * 100, 1)
            }
        else:
            yoy = None
            
    if selected_month:
        cursor.execute("""
            SELECT
                EXTRACT(DOW FROM transaction_date) AS dow,
                TO_CHAR(transaction_date, 'Day') AS day_name,
                SUM(amount) AS total
            FROM transactions
            WHERE transaction_type = 'expense'
            AND EXTRACT(YEAR FROM transaction_date) = %s
            AND EXTRACT(MONTH FROM transaction_date) = %s
            GROUP BY dow, day_name
            ORDER BY dow
        """, (filter_year, filter_month))
    else:
        cursor.execute("""
            SELECT
                EXTRACT(DOW FROM transaction_date) AS dow,
                TO_CHAR(transaction_date, 'Day') AS day_name,
                SUM(amount) AS total
            FROM transactions
            WHERE transaction_type = 'expense'
            GROUP BY dow, day_name
            ORDER BY dow
        """)
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
        WHERE transaction_type = 'expense'
        AND transaction_date >= NOW() - INTERVAL '6 months'
        AND category_id IS NOT NULL
        GROUP BY category_id, DATE_TRUNC('month', transaction_date)
    ) monthly
    JOIN categories c ON c.id = monthly.category_id
    GROUP BY c.name
    HAVING COUNT(DISTINCT month) >= 1
    ORDER BY suggested_budget DESC
    """)
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

@app.route('/recurring/process')
def process_recurring():
    today = datetime.today().date()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, amount, description, category_id, account_id,
               transaction_type, frequency, next_due
        FROM transactions
        WHERE is_recurring = true AND next_due <= %s
    """, (today,))
    due = cursor.fetchall()
    count = 0
    for t in due:
        tid, amount, desc, cat_id, acc_id, ttype, freq, next_due = t
        cursor.execute("""
            INSERT INTO transactions
                (amount, description, category_id, account_id,
                 transaction_date, transaction_type,
                 is_recurring, frequency, next_due)
            VALUES (%s,%s,%s,%s,%s,%s,true,%s,%s)
        """, (
            amount, desc, cat_id, acc_id, next_due, ttype, freq,
            (next_due + relativedelta(months=1)).strftime('%Y-%m-%d')
            if freq == 'monthly' else
            (next_due + timedelta(weeks=1)).strftime('%Y-%m-%d')
        ))
        cursor.execute(
            "UPDATE transactions SET next_due = %s WHERE id = %s",
            (
                (next_due + relativedelta(months=1)).strftime('%Y-%m-%d')
                if freq == 'monthly' else
                (next_due + timedelta(weeks=1)).strftime('%Y-%m-%d'),
                tid
            )
        )
        count += 1
    conn.commit()
    cursor.close()
    conn.close()
    flash(f'{count} recurring transaction(s) processed')
    return redirect('/transactions')

@app.route('/transactions/export')
def export_transactions():
  selected_month = request.args.get('month')
  search = request.args.get('search', '').strip()
  conn = get_db_connection()
  cursor = conn.cursor()
  filters = []
  params = []
  if selected_month:
    year, month = selected_month.split('-')
    filters.append("EXTRACT(YEAR FROM t.transaction_date) = %s AND EXTRACT(MONTH FROM t.transaction_date) = %s")
    params.extend([year, month])
  if search:
    filters.append("t.description ILIKE %s")
    params.append(f'%{search}%')
  where_clause = "WHERE " + " AND ".join(filters) if filters else ""
  cursor.execute(f"""
    SELECT t.transaction_date, t.transaction_type, t.amount,
           t.description, c.name, a.account_name
    FROM transactions t
    LEFT JOIN categories c ON t.category_id = c.id
    LEFT JOIN account a ON t.account_id = a.account_id
    {where_clause}
    ORDER BY t.transaction_date DESC, t.id DESC
  """, params)
  rows = cursor.fetchall()
  cursor.close()
  conn.close()
  output = io.StringIO()
  writer = csv.writer(output)
  writer.writerow(['Date', 'Type', 'Amount', 'Description', 'Category', 'Account'])
  writer.writerows(rows)
  output.seek(0)
  response = make_response(output.getvalue())
  response.headers['Content-Disposition'] = 'attachment; filename=transactions.csv'
  response.headers['Content-Type'] = 'text/csv'
  return response

@app.route('/admin/backup')
def backup_database():
  db_host = os.getenv('DB_HOST', 'db')
  db_name = os.getenv('DB_NAME', 'budget')
  db_user = os.getenv('DB_USER', 'admin')
  db_password = os.getenv('DB_PASSWORD', '')
  env = os.environ.copy()
  env['PGPASSWORD'] = db_password
  result = subprocess.run(
    ['pg_dump', '-h', db_host, '-U', db_user, db_name],
    capture_output=True,
    env=env
  )
  if result.returncode != 0:
    flash('Backup failed')
    return redirect('/')
  from datetime import date
  filename = f'budget_backup_{date.today()}.sql'
  response = make_response(result.stdout)
  response.headers['Content-Disposition'] = f'attachment; filename={filename}'
  response.headers['Content-Type'] = 'application/octet-stream'
  return response

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
@app.route('/settings')
def settings():
    return render_template('settings.html')