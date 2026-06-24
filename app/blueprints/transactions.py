import calendar
import csv
import io
import math
from datetime import datetime, date, timedelta
from urllib.parse import urlencode
from dateutil.relativedelta import relativedelta
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, make_response, abort
)
from flask_login import login_required, current_user
from app import limiter
from app.db import get_db_connection, db_cursor
from app.helpers import recent_months, is_htmx, hx_toast, ai_enabled
from app.ai import parse_transaction_text, ParseError

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


def validate_category_account(cursor, user_id, category_id, account_id):
    """Ownership guard for write-side FK ids (v10.1.1 IDOR fix). For each
    non-null id, confirm the row belongs to user_id; return a list of error
    strings for any that don't, so callers fold it into their existing
    validation-error path. Mirrors the budgets `/budgets/set` guard, but for
    the transaction + schedule forms (which previously trusted the posted ids).
    Shared with blueprints/schedules.py."""
    errors = []
    if category_id is not None:
        cursor.execute("SELECT 1 FROM categories WHERE id = %s AND user_id = %s",
                       (category_id, user_id))
        if cursor.fetchone() is None:
            errors.append('Invalid category')
    if account_id is not None:
        cursor.execute("SELECT 1 FROM account WHERE account_id = %s AND user_id = %s",
                       (account_id, user_id))
        if cursor.fetchone() is None:
            errors.append('Invalid account')
    return errors


# Cells that begin with these chars are interpreted as formulas by Excel/Sheets;
# prefixing an apostrophe neutralizes CSV formula injection (v10.1.1).
_CSV_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def _csv_safe(value):
    """Neutralize CSV/spreadsheet formula injection: prefix a leading apostrophe
    when a string cell starts with a formula-trigger char. Non-strings pass
    through unchanged. Pure (unit-testable)."""
    if isinstance(value, str) and value and value[0] in _CSV_FORMULA_PREFIXES:
        return "'" + value
    return value


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
        is_adjustment = request.form.get('is_adjustment') == 'true'
        if not errors:
            with db_cursor() as cursor:
                errors += validate_category_account(cursor, current_user.id, category_id, account_id)
        if errors:
            for error in errors:
                flash(error)
            return redirect(url_for('transactions.new_transaction'))
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO transactions (amount, description, transaction_date, category_id, account_id, transaction_type, is_adjustment, user_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (amount, description, transaction_date, category_id, account_id, transaction_type, is_adjustment, current_user.id)
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
    return render_template('new_transaction.html', categories=all_categories,
                           accounts=all_accounts, ai_enabled=ai_enabled())


@bp.route('/transactions/parse', methods=['POST'])
@limiter.limit("10 per minute")
@login_required
def parse_transaction():
    """v9.0 NL quick-add: parse free text into a pre-filled Add form fragment.

    Page-agnostic — both Home and the Add page post here and swap the result
    into #txn-form-wrap. Any failure (no key, API error, unparseable) falls back
    to an empty form + an error toast, so the manual form is always usable."""
    text = request.form.get('text', '').strip()
    with db_cursor() as cursor:
        cursor.execute("SELECT id, name FROM categories WHERE user_id = %s ORDER BY name", (current_user.id,))
        all_categories = cursor.fetchall()
        cursor.execute("SELECT account_id, account_name FROM account WHERE user_id = %s ORDER BY account_name", (current_user.id,))
        all_accounts = cursor.fetchall()

    def _form(prefill):
        return make_response(render_template(
            'partials/_transaction_form.html',
            categories=all_categories, accounts=all_accounts, prefill=prefill))

    if not text:
        return hx_toast(_form({}), 'Type what you bought first', 'error')
    try:
        prefill = parse_transaction_text(text, all_categories, all_accounts)
    except ParseError:
        return hx_toast(_form({}), "Couldn't parse that — fill it in manually", 'error')
    return hx_toast(_form(prefill), 'Parsed — review and save')


PER_PAGE = 25


def _filter_qs(selected_month, search, page):
    """Query string carrying the current History filters so inline actions can
    re-render the same page/filter slice after a mutation."""
    params = {}
    if selected_month:
        params['month'] = selected_month
    if search:
        params['search'] = search
    if page and page != 1:
        params['page'] = page
    return urlencode(params)


def _current_filters():
    """Read the History filters off the current request (query string)."""
    return (
        request.args.get('month'),
        request.args.get('search', '').strip(),
        int(request.args.get('page', 1) or 1),
    )


def _load_history(user_id, selected_month, search, page, per_page=PER_PAGE):
    """Return (rows_with_running_balance, total, total_pages) for one page of a
    user's transaction history under the given filters."""
    offset = (page - 1) * per_page
    conn = get_db_connection()
    cursor = conn.cursor()
    filters = ["t.user_id = %s"]
    params = [user_id]
    if selected_month:
        year, month = selected_month.split('-')
        filters.append("EXTRACT(YEAR FROM t.transaction_date) = %s AND EXTRACT(MONTH FROM t.transaction_date) = %s")
        params.extend([year, month])
    if search:
        filters.append("t.description ILIKE %s")
        params.append(f'%{search}%')
    where_clause = "WHERE " + " AND ".join(filters)
    cursor.execute(f"SELECT COUNT(*) FROM transactions t {where_clause}", params)
    total = cursor.fetchone()[0]
    total_pages = math.ceil(total / per_page) if total > 0 else 1
    cursor.execute(f"""
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
    """, params + [per_page, offset])
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
    return transactions_with_balance, total, total_pages


def render_history_tbody():
    """Re-render the History <tbody> for the request's current filters — shared
    by every inline mutation (and by the transfers blueprint) so the per-page
    running balance is always recomputed."""
    selected_month, search, page = _current_filters()
    rows, _total, _pages = _load_history(current_user.id, selected_month, search, page)
    return render_template('partials/_transactions_tbody.html',
                           transactions=rows,
                           frequency_labels=FREQUENCY_LABELS,
                           filter_qs=_filter_qs(selected_month, search, page))


@bp.route('/transactions')
@login_required
def transactions():
    from app.blueprints.schedules import run_due_schedules  # lazy: avoids import cycle
    run_due_schedules(current_user.id)
    selected_month = request.args.get('month')
    search = request.args.get('search', '').strip()
    page = int(request.args.get('page', 1))
    months = recent_months()
    rows, total, total_pages = _load_history(current_user.id, selected_month, search, page)
    return render_template('history.html',
        transactions=rows,
        months=months,
        selected_month=selected_month,
        search=search,
        page=page,
        total_pages=total_pages,
        total=total,
        frequency_labels=FREQUENCY_LABELS,
        filter_qs=_filter_qs(selected_month, search, page),
    )


@bp.route('/transactions/rows')
@login_required
def transaction_rows():
    """Restore point for Cancel + a generic tbody refresh."""
    return make_response(render_history_tbody())


@bp.route('/transactions/<int:transaction_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_transaction(transaction_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM transactions WHERE id = %s AND user_id = %s", (transaction_id, current_user.id))
    if cursor.fetchone() is None:
        cursor.close(); conn.close()
        abort(404)
    cursor.execute("SELECT id, name FROM categories WHERE user_id = %s ORDER BY name", (current_user.id,))
    all_categories = cursor.fetchall()
    cursor.execute("SELECT account_id, account_name FROM account WHERE user_id = %s ORDER BY account_name", (current_user.id,))
    all_accounts = cursor.fetchall()

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
        if not errors:
            errors += validate_category_account(cursor, current_user.id, category_id, account_id)
        if errors:
            cursor.close(); conn.close()
            txn = (transaction_id, amount_str, description, transaction_date,
                   int(category_id) if category_id else None,
                   int(account_id) if account_id else None,
                   transaction_type, is_adjustment)
            return render_template('partials/_transaction_edit_row.html',
                                   txn=txn, categories=all_categories,
                                   accounts=all_accounts,
                                   filter_qs=request.query_string.decode(),
                                   errors=errors)
        amount = float(amount_str)
        try:
            cursor.execute(
                "UPDATE transactions SET amount=%s, description=%s, transaction_date=%s, category_id=%s, account_id=%s, transaction_type=%s, is_adjustment=%s WHERE id=%s AND user_id=%s",
                (amount, description, transaction_date, category_id, account_id, transaction_type, is_adjustment, transaction_id, current_user.id)
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            cursor.close(); conn.close()
            txn = (transaction_id, amount_str, description, transaction_date,
                   int(category_id) if category_id else None,
                   int(account_id) if account_id else None,
                   transaction_type, is_adjustment)
            return render_template('partials/_transaction_edit_row.html',
                                   txn=txn, categories=all_categories,
                                   accounts=all_accounts,
                                   filter_qs=request.query_string.decode(),
                                   errors=[str(e)])
        cursor.close(); conn.close()
        return hx_toast(make_response(render_history_tbody()), 'Transaction updated')

    cursor.execute("SELECT id, amount, description, transaction_date, category_id, account_id, transaction_type, is_adjustment FROM transactions WHERE id = %s AND user_id = %s", (transaction_id, current_user.id))
    txn = cursor.fetchone()
    cursor.close()
    conn.close()
    return render_template('partials/_transaction_edit_row.html',
                           txn=txn, categories=all_categories, accounts=all_accounts,
                           filter_qs=request.query_string.decode())


@bp.route('/transactions/<int:transaction_id>', methods=['DELETE'])
@login_required
def delete_transaction(transaction_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM transactions WHERE id = %s AND user_id = %s", (transaction_id, current_user.id))
    if cursor.fetchone() is None:
        cursor.close(); conn.close()
        abort(404)
    try:
        cursor.execute("DELETE FROM transactions WHERE id = %s AND user_id = %s", (transaction_id, current_user.id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        cursor.close(); conn.close()
        return hx_toast(make_response(render_history_tbody()), f'Error: {e}', 'error')
    cursor.close()
    conn.close()
    return hx_toast(make_response(render_history_tbody()), 'Transaction deleted')


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
    writer.writerows([_csv_safe(cell) for cell in row] for row in rows)
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = 'attachment; filename=transactions.csv'
    response.headers['Content-Type'] = 'text/csv'
    return response
