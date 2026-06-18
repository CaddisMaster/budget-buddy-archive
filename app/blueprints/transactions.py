import calendar
import csv
import io
import math
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, make_response, abort
)
from flask_login import login_required, current_user
from app.db import get_db_connection

bp = Blueprint('transactions', __name__)

# Recurring frequencies the app accepts. 'semimonthly' is the only one that
# needs a second pay day (recur_second_day); the rest are fixed intervals.
VALID_FREQUENCIES = ('weekly', 'biweekly', 'semimonthly', 'monthly', 'quarterly', 'annually')

# Human-friendly labels for the recurring badge in the history table.
FREQUENCY_LABELS = {
    'weekly': 'Weekly',
    'biweekly': 'Bi-weekly',
    'semimonthly': 'Semi-monthly',
    'monthly': 'Monthly',
    'quarterly': 'Quarterly',
    'annually': 'Annually',
}


def _clamp_to_month(year, month, day):
    """Build a date, clamping the day to the last valid day of that month
    (so a '31st' pay day lands on the 28th/30th in shorter months)."""
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def compute_next_due(current, frequency, anchor_day=None, second_day=None):
    """Return the next due date (a date) strictly after `current` for the given
    frequency. For 'semimonthly', anchor_day and second_day are the two pay days
    of the month (e.g. 15 and 31); the function alternates between them."""
    if frequency == 'weekly':
        return current + timedelta(weeks=1)
    if frequency == 'biweekly':
        return current + timedelta(weeks=2)
    if frequency == 'monthly':
        return current + relativedelta(months=1)
    if frequency == 'quarterly':
        return current + relativedelta(months=3)
    if frequency == 'annually':
        return current + relativedelta(years=1)
    if frequency == 'semimonthly' and anchor_day and second_day:
        lo, hi = sorted((anchor_day, second_day))
        # Compare against the clamped date, so a "31 = last day" pay day that
        # lands on the 28th/30th doesn't keep re-targeting the same month.
        hi_this_month = _clamp_to_month(current.year, current.month, hi)
        if current < hi_this_month:
            return hi_this_month
        nxt = current + relativedelta(months=1)
        return _clamp_to_month(nxt.year, nxt.month, lo)
    # Fallback keeps a recurring row moving forward even on unexpected data.
    return current + relativedelta(months=1)


def run_process_recurring(user_id):
    today = datetime.today().date()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, amount, description, category_id, account_id,
               transaction_type, frequency, next_due, transaction_date,
               recur_second_day, is_adjustment
        FROM transactions
        WHERE is_recurring = true AND next_due <= %s AND user_id = %s
    """, (today, user_id))
    due = cursor.fetchall()
    for t in due:
        (tid, amount, desc, cat_id, acc_id, ttype, freq, next_due,
         anchor_date, second_day, is_adjustment) = t
        new_next_due = compute_next_due(
            next_due, freq, anchor_day=anchor_date.day, second_day=second_day
        )
        # The generated occurrence is a plain transaction, not a recurring
        # template — only the original row carries the recurrence.
        cursor.execute("""
            INSERT INTO transactions
                (amount, description, category_id, account_id,
                 transaction_date, transaction_type,
                 is_recurring, is_adjustment, user_id)
            VALUES (%s, %s, %s, %s, %s, %s, false, %s, %s)
        """, (amount, desc, cat_id, acc_id, next_due, ttype, is_adjustment, user_id))
        cursor.execute(
            "UPDATE transactions SET next_due = %s WHERE id = %s AND user_id = %s",
            (new_next_due, tid, user_id)
        )
    conn.commit()
    cursor.close()
    conn.close()


@bp.route('/transactions/new', methods=['GET', 'POST'])
@login_required
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
        is_recurring = request.form.get('is_recurring') == 'true'
        is_adjustment = request.form.get('is_adjustment') == 'true'
        frequency = request.form.get('frequency') if is_recurring else None
        second_day = None
        if is_recurring:
            if frequency not in VALID_FREQUENCIES:
                errors.append('Please choose a valid recurring frequency')
            elif frequency == 'semimonthly':
                second_day_raw = request.form.get('recur_second_day', '').strip()
                try:
                    second_day = int(second_day_raw)
                    if not 1 <= second_day <= 31:
                        errors.append('Second pay day must be between 1 and 31')
                        second_day = None
                except ValueError:
                    errors.append('Second pay day is required for a semi-monthly schedule')
        if errors:
            for error in errors:
                flash(error)
            return redirect(url_for('transactions.new_transaction'))
        next_due = None
        if is_recurring and frequency:
            start = datetime.strptime(transaction_date, '%Y-%m-%d').date()
            next_due = compute_next_due(
                start, frequency, anchor_day=start.day, second_day=second_day
            )
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO transactions (amount, description, transaction_date, category_id, account_id, transaction_type, is_recurring, frequency, next_due, recur_second_day, is_adjustment, user_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (amount, description, transaction_date, category_id, account_id, transaction_type, is_recurring, frequency, next_due, second_day, is_adjustment, current_user.id)
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
        return redirect(url_for('transactions.new_transaction'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM categories WHERE user_id = %s ORDER BY name", (current_user.id,))
    all_categories = cursor.fetchall()
    cursor.execute("SELECT account_id, account_name FROM account WHERE user_id = %s ORDER BY account_name", (current_user.id,))
    all_accounts = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('new_transaction.html', categories=all_categories, accounts=all_accounts)


@bp.route('/recurring/process')
@login_required
def process_recurring():
    run_process_recurring(current_user.id)
    flash('Recurring transactions processed')
    return redirect('/transactions')


@bp.route('/transactions')
@login_required
def transactions():
    run_process_recurring(current_user.id)
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
    filters = ["t.user_id = %s"]
    params = [current_user.id]
    if selected_month:
        year, month = selected_month.split('-')
        filters.append("EXTRACT(YEAR FROM t.transaction_date) = %s AND EXTRACT(MONTH FROM t.transaction_date) = %s")
        params.extend([year, month])
    if search:
        filters.append("t.description ILIKE %s")
        params.append(f'%{search}%')
    where_clause = "WHERE " + " AND ".join(filters)
    count_query = f"SELECT COUNT(*) FROM transactions t {where_clause}"
    cursor.execute(count_query, params)
    total = cursor.fetchone()[0]
    total_pages = math.ceil(total / per_page) if total > 0 else 1
    main_query = f"""
        SELECT t.id, t.amount, t.description, c.name, a.account_name,
            t.transaction_date, t.transaction_type,
            t.is_recurring, t.frequency, t.is_adjustment,
            t.is_transfer, t.transfer_group_id
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
        total=total,
        frequency_labels=FREQUENCY_LABELS
    )


@bp.route('/transactions/cancel-recurring/<int:id>', methods=['POST'])
@login_required
def cancel_recurring(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM transactions WHERE id = %s AND user_id = %s", (id, current_user.id))
    if cursor.fetchone() is None:
        cursor.close(); conn.close()
        abort(404)
    cursor.execute(
        "UPDATE transactions SET is_recurring=false, frequency=NULL, next_due=NULL WHERE id=%s AND user_id=%s",
        (id, current_user.id)
    )
    conn.commit()
    cursor.close()
    conn.close()
    flash('Recurring cancelled')
    return redirect('/transactions')


@bp.route('/transactions/edit/<int:transaction_id>', methods=['GET', 'POST'])
@login_required
def edit_transaction(transaction_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM transactions WHERE id = %s AND user_id = %s", (transaction_id, current_user.id))
    if cursor.fetchone() is None:
        cursor.close(); conn.close()
        abort(404)
    if request.method == 'POST':
        amount_str = request.form['amount'].strip()
        description = request.form['description'].strip()
        transaction_date = request.form['transaction_date'].strip()
        category_id = request.form.get('category_id') or None
        account_id = request.form.get('account_id') or None
        transaction_type = request.form.get('transaction_type', 'expense')
        is_adjustment = request.form.get('is_adjustment') == 'true'
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
        if transaction_type not in ('income', 'expense'):
            errors.append('Transaction type must be income or expense')
        if errors:
            cursor.close(); conn.close()
            for e in errors:
                flash(e)
            return redirect(url_for('transactions.edit_transaction', transaction_id=transaction_id))
        amount = float(amount_str)
        try:
            cursor.execute(
                "UPDATE transactions SET amount=%s, description=%s, transaction_date=%s, category_id=%s, account_id=%s, transaction_type=%s, is_adjustment=%s WHERE id=%s AND user_id=%s",
                (amount, description, transaction_date, category_id, account_id, transaction_type, is_adjustment, transaction_id, current_user.id)
            )
            conn.commit()
            flash('Transaction updated successfully')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect(url_for('transactions.transactions'))
    cursor.execute("SELECT id, amount, description, transaction_date, category_id, account_id, transaction_type, is_adjustment FROM transactions WHERE id = %s AND user_id = %s", (transaction_id, current_user.id))
    transaction = cursor.fetchone()
    cursor.execute("SELECT id, name FROM categories WHERE user_id = %s ORDER BY name", (current_user.id,))
    all_categories = cursor.fetchall()
    cursor.execute("SELECT account_id, account_name FROM account WHERE user_id = %s ORDER BY account_name", (current_user.id,))
    all_accounts = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('edit_transaction.html', transaction=transaction, categories=all_categories, accounts=all_accounts)


@bp.route('/transactions/delete/<int:transaction_id>', methods=['GET', 'POST'])
@login_required
def delete_transaction(transaction_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM transactions WHERE id = %s AND user_id = %s", (transaction_id, current_user.id))
    if cursor.fetchone() is None:
        cursor.close(); conn.close()
        abort(404)
    if request.method == 'POST':
        try:
            cursor.execute("DELETE FROM transactions WHERE id = %s AND user_id = %s", (transaction_id, current_user.id))
            conn.commit()
            flash('Transaction deleted')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect(url_for('transactions.transactions'))
    cursor.execute("""
        SELECT t.id, t.amount, t.description, t.transaction_date, t.transaction_type
        FROM transactions t WHERE t.id = %s AND t.user_id = %s
    """, (transaction_id, current_user.id))
    transaction = cursor.fetchone()
    cursor.close()
    conn.close()
    return render_template('delete_transaction.html', transaction=transaction)


@bp.route('/transactions/export')
@login_required
def export_transactions():
    selected_month = request.args.get('month')
    search = request.args.get('search', '').strip()
    conn = get_db_connection()
    cursor = conn.cursor()
    filters = ["t.user_id = %s"]
    params = [current_user.id]
    if selected_month:
        year, month = selected_month.split('-')
        filters.append("EXTRACT(YEAR FROM t.transaction_date) = %s AND EXTRACT(MONTH FROM t.transaction_date) = %s")
        params.extend([year, month])
    if search:
        filters.append("t.description ILIKE %s")
        params.append(f'%{search}%')
    where_clause = "WHERE " + " AND ".join(filters)
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
