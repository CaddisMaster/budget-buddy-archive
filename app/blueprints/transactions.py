import calendar
import csv
import io
import math
from datetime import datetime, date, timedelta
from urllib.parse import urlencode
from dateutil.relativedelta import relativedelta
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, make_response,
    abort, current_app
)
from flask_login import login_required, current_user
from app import limiter
from app.db import get_db_connection, db_cursor
from app.helpers import (
    recent_months, is_htmx, hx_toast, ai_enabled, parse_positive_amount,
    parse_month_param, parse_page_param, parse_int_param, GENERIC_ERROR
)
from app.ai import parse_transaction_text, classify_transactions, ParseError

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
        description = request.form['description'].strip()
        transaction_date = request.form['transaction_date'].strip()
        category_id = parse_int_param(request.form.get('category_id'))
        account_id = parse_int_param(request.form.get('account_id'))
        transaction_type = request.form.get('transaction_type', 'expense')
        errors = []
        amount, amount_error = parse_positive_amount(request.form.get('amount'))
        if amount_error:
            errors.append(amount_error)
        if not transaction_date:
            errors.append('Date is required')
        else:
            try:
                datetime.strptime(transaction_date, '%Y-%m-%d')
            except ValueError:
                errors.append('Date must be a valid date')
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
        except Exception:
            current_app.logger.exception('new_transaction failed')
            flash(GENERIC_ERROR)
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
        parse_month_param(request.args.get('month')),
        request.args.get('search', '').strip(),
        parse_page_param(request.args.get('page', 1)),
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
    from app.blueprints.transfers import run_due_transfers
    run_due_schedules(current_user.id)
    run_due_transfers(current_user.id)
    selected_month, search, page = _current_filters()
    months = recent_months()
    rows, total, total_pages = _load_history(current_user.id, selected_month, search, page)
    cleanup_enabled = ai_enabled()
    uncategorized_count = count_uncategorized(current_user.id) if cleanup_enabled else 0
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
        cleanup_enabled=cleanup_enabled,
        uncategorized_count=uncategorized_count,
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
        category_id = parse_int_param(request.form.get('category_id'))
        account_id = parse_int_param(request.form.get('account_id'))
        transaction_type = request.form.get('transaction_type', 'expense')
        is_adjustment = request.form.get('is_adjustment') == 'true'
        errors = []
        amount, amount_error = parse_positive_amount(amount_str)
        if amount_error:
            errors.append(amount_error)
        if not transaction_date:
            errors.append('Date is required')
        else:
            try:
                datetime.strptime(transaction_date, '%Y-%m-%d')
            except ValueError:
                errors.append('Date must be a valid date')
        if transaction_type not in ('income', 'expense'):
            errors.append('Transaction type must be income or expense')
        if not errors:
            errors += validate_category_account(cursor, current_user.id, category_id, account_id)
        if errors:
            cursor.close(); conn.close()
            txn = (transaction_id, amount_str, description, transaction_date,
                   category_id, account_id, transaction_type, is_adjustment)
            return render_template('partials/_transaction_edit_row.html',
                                   txn=txn, categories=all_categories,
                                   accounts=all_accounts,
                                   filter_qs=request.query_string.decode(),
                                   errors=errors)
        try:
            cursor.execute(
                "UPDATE transactions SET amount=%s, description=%s, transaction_date=%s, category_id=%s, account_id=%s, transaction_type=%s, is_adjustment=%s WHERE id=%s AND user_id=%s",
                (amount, description, transaction_date, category_id, account_id, transaction_type, is_adjustment, transaction_id, current_user.id)
            )
            conn.commit()
        except Exception:
            conn.rollback()
            cursor.close(); conn.close()
            current_app.logger.exception('edit_transaction failed')
            txn = (transaction_id, amount_str, description, transaction_date,
                   category_id, account_id, transaction_type, is_adjustment)
            return render_template('partials/_transaction_edit_row.html',
                                   txn=txn, categories=all_categories,
                                   accounts=all_accounts,
                                   filter_qs=request.query_string.decode(),
                                   errors=[GENERIC_ERROR])
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
    except Exception:
        conn.rollback()
        cursor.close(); conn.close()
        current_app.logger.exception('delete transaction failed')
        return hx_toast(make_response(render_history_tbody()), GENERIC_ERROR, 'error')
    cursor.close()
    conn.close()
    return hx_toast(make_response(render_history_tbody()), 'Transaction deleted')


@bp.route('/transactions/export')
@login_required
def export_transactions():
    selected_month = parse_month_param(request.args.get('month'))
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


# ---------------------------------------------------------------------------
# v10.5 — Auto-Categorize & Cleanup.
#
# A banner on History counts uncategorized rows; "Review" runs ONE batched Sonnet
# call (app/ai.py::classify_transactions) over the candidate rows and renders a
# review list of PROPOSALS the user bulk-confirms. The app picks the candidates
# and owns the write (ownership + validate_category_account guards); the model
# only proposes. Nothing auto-applies. Gated on ai_enabled(); degrades to an
# error toast, never a broken History page.
# ---------------------------------------------------------------------------

CLEANUP_BATCH_CAP = 50          # max rows sent to the model per scan
CLEANUP_RECENT_DAYS = 90        # window of categorized rows eligible for re-suggestion


def count_uncategorized(user_id):
    """How many of the user's real EXPENSE transactions have no category — the
    deterministic number the History banner shows. Scoped to expenses on purpose:
    categories in Budget Buddy are effectively expense categories (budgets, the
    category breakdown, and analytics all filter to expense), so an uncategorized
    income row has no downstream effect AND can never get a suggestion — counting
    it would make the banner promise a fix the scan can't deliver."""
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT COUNT(*) FROM transactions
            WHERE user_id = %s AND category_id IS NULL
            AND transaction_type = 'expense'
            AND is_transfer = false AND is_adjustment = false
        """, (user_id,))
        return cursor.fetchone()[0]


def _load_cleanup_candidates(user_id, cap=CLEANUP_BATCH_CAP):
    """Rows to hand the classifier, newest first: ALL uncategorized rows first,
    then fill any remaining slots (up to `cap`) with recently-categorized rows so
    obviously mis-filed ones can be re-suggested. Scoped to EXPENSES (see
    count_uncategorized) and excludes transfers/adjustments. Returns row dicts
    carrying current_category (name or None) for the payload."""
    candidates = []
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT t.id, t.description, t.amount, t.transaction_type,
                   t.category_id, c.name
            FROM transactions t
            LEFT JOIN categories c ON t.category_id = c.id
            WHERE t.user_id = %s AND t.category_id IS NULL
            AND t.transaction_type = 'expense'
            AND t.is_transfer = false AND t.is_adjustment = false
            ORDER BY t.transaction_date DESC, t.id DESC
            LIMIT %s
        """, (user_id, cap))
        candidates = list(cursor.fetchall())

        remaining = cap - len(candidates)
        if remaining > 0:
            cursor.execute("""
                SELECT t.id, t.description, t.amount, t.transaction_type,
                       t.category_id, c.name
                FROM transactions t
                JOIN categories c ON t.category_id = c.id
                WHERE t.user_id = %s AND t.category_id IS NOT NULL
                AND t.transaction_type = 'expense'
                AND t.is_transfer = false AND t.is_adjustment = false
                AND t.transaction_date >= %s
                ORDER BY t.transaction_date DESC, t.id DESC
                LIMIT %s
            """, (user_id, date.today() - timedelta(days=CLEANUP_RECENT_DAYS), remaining))
            candidates += list(cursor.fetchall())

    rows = []
    for r in candidates:
        rows.append({
            'id': r[0],
            'description': r[1] or '',
            'amount': float(r[2]),
            'type': r[3],
            'current_category_id': r[4],
            'current_category': r[5],
        })
    return rows


def _cleanup_payload(rows):
    """The trimmed view the model sees — never expose internal ids beyond the row
    id it must echo back, and only the fields it needs to classify."""
    return [{
        'id': r['id'],
        'description': r['description'],
        'amount': r['amount'],
        'type': r['type'],
        'current_category': r['current_category'],
    } for r in rows]


@bp.route('/transactions/cleanup/scan', methods=['POST'])
@limiter.limit("10 per minute")
@login_required
def cleanup_scan():
    """Run one batched classification over the candidate rows and render the
    review list. Surfaces a suggestion when the row is uncategorized (any
    confidence) or when it's categorized and the model proposes a DIFFERENT
    category at HIGH confidence. Any failure / nothing to review falls back to an
    error toast without clearing the panel."""
    if not ai_enabled():
        abort(404)

    rows = _load_cleanup_candidates(current_user.id)
    by_id = {r['id']: r for r in rows}

    with db_cursor() as cursor:
        cursor.execute("SELECT id, name FROM categories WHERE user_id = %s ORDER BY name",
                       (current_user.id,))
        all_categories = cursor.fetchall()

    def _toast_only(message):
        resp = hx_toast(make_response('', 200), message, 'error')
        resp.headers['HX-Reswap'] = 'none'
        return resp

    if not rows:
        return _toast_only('Nothing to review — everything is categorized')

    try:
        suggestions = classify_transactions(_cleanup_payload(rows), all_categories)
    except ParseError:
        return _toast_only("Couldn't scan right now — try again")

    items = []
    for s in suggestions:
        row = by_id.get(s['id'])
        if row is None:
            continue
        current_id = row['current_category_id']
        if current_id is None:
            pass  # uncategorized — always surface
        elif s['category_id'] == current_id or s['confidence'] != 'high':
            continue  # categorized: only high-confidence disagreements
        items.append({
            'id': row['id'],
            'description': row['description'],
            'amount': row['amount'],
            'type': row['type'],
            'current_category': row['current_category'],
            'suggested_id': s['category_id'],
            'suggested_name': s['category_name'],
            'confidence': s['confidence'],
        })

    if not items:
        return _toast_only('Nothing to review — no confident suggestions')

    resp = make_response(render_template(
        'partials/_cleanup_review.html', items=items, categories=all_categories))
    return hx_toast(resp, f'{len(items)} suggestion{"s" if len(items) != 1 else ""} to review')


@bp.route('/transactions/cleanup/apply', methods=['POST'])
@limiter.limit("10 per minute")
@login_required
def cleanup_apply():
    """Apply the checked suggestions — for each checked row, set its category to
    the chosen one. Guards transaction ownership (WHERE id AND user_id) AND
    validates the chosen category belongs to the user (validate_category_account)
    before writing, so the model/form can't reach another user's data. Returns
    the refreshed History tbody + an updated banner. Nothing auto-applies — only
    rows the user checked are written."""
    if not ai_enabled():
        abort(404)

    checked = request.form.getlist('row_id')
    applied = 0
    with db_cursor(commit=True) as cursor:
        for rid in checked:
            try:
                row_id = int(rid)
            except (TypeError, ValueError):
                continue
            category_id = request.form.get(f'category_{row_id}') or None
            if category_id is None:
                continue
            try:
                category_id = int(category_id)
            except (TypeError, ValueError):
                continue
            if validate_category_account(cursor, current_user.id, category_id, None):
                continue  # not the user's category — skip (write-side IDOR guard)
            cursor.execute(
                "UPDATE transactions SET category_id = %s WHERE id = %s AND user_id = %s",
                (category_id, row_id, current_user.id))
            applied += cursor.rowcount

    resp = make_response(render_history_tbody() + _cleanup_banner_oob(current_user.id))
    message = (f'{applied} transaction{"s" if applied != 1 else ""} categorized'
               if applied else 'Nothing applied')
    return hx_toast(resp, message, 'success' if applied else 'error')


def _cleanup_banner_oob(user_id):
    """The History banner re-rendered as an out-of-band swap, so applying updates
    the count (and clears the review panel) alongside the refreshed tbody."""
    return render_template('partials/_cleanup_banner.html',
                           uncategorized_count=count_uncategorized(user_id),
                           cleanup_enabled=True, oob=True)
