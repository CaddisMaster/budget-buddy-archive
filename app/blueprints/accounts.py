from datetime import date

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort,
    make_response
)
from flask_login import login_required, current_user
from app.db import get_db_connection, db_cursor
from app.helpers import is_htmx, hx_toast, parse_signed_amount

bp = Blueprint('accounts', __name__)

VALID_ACCOUNT_TYPES = ('Credit Card', 'Debit Card', 'Bank Account')

# Days since the last check-in before an account reads as stale on /accounts.
CHECKIN_STALE_DAYS = 30

ACCOUNT_ROW_SQL = """
    SELECT a.account_id, a.account_name, a.type,
        COALESCE(SUM(CASE WHEN t.transaction_type = 'income'
            THEN t.amount ELSE -t.amount END), 0) AS balance,
        a.last_checked_in,
        (a.last_checked_in IS NULL
            OR a.last_checked_in < CURRENT_DATE - {stale_days}) AS checkin_stale
    FROM account a
    LEFT JOIN transactions t ON a.account_id = t.account_id AND t.user_id = a.user_id
    WHERE a.user_id = %s {extra}
    GROUP BY a.account_id, a.account_name, a.type, a.last_checked_in
    ORDER BY a.account_name
""".replace('{stale_days}', str(CHECKIN_STALE_DAYS))


def _fetch_account_row(cursor, account_id):
    """Single account row (with balance) for the current user, or 404."""
    cursor.execute(ACCOUNT_ROW_SQL.format(extra="AND a.account_id = %s"),
                   (current_user.id, account_id))
    row = cursor.fetchone()
    if row is None:
        abort(404)
    return row


def _validate(name, account_type):
    if not name:
        return 'Name is required'
    if len(name) > 50:
        return 'Name must be 50 characters or fewer'
    if account_type not in VALID_ACCOUNT_TYPES:
        return 'Invalid account type'
    return None


@bp.route('/accounts', methods=['GET', 'POST'])
@login_required
def accounts():
    if request.method == 'POST':
        name = request.form['name'].strip()
        account_type = request.form.get('type', '').strip()
        error = _validate(name, account_type)
        if error:
            if is_htmx():
                return hx_toast(make_response('', 200), error, 'error')
            flash(error)
            return redirect(url_for('accounts.accounts'))
        try:
            with db_cursor(commit=True) as cursor:
                cursor.execute(
                    "INSERT INTO account (account_name, type, user_id) "
                    "VALUES (%s, %s, %s) RETURNING account_id",
                    (name, account_type, current_user.id),
                )
                new_id = cursor.fetchone()[0]
        except Exception as e:
            if is_htmx():
                return hx_toast(make_response('', 200), f'Error: {e}', 'error')
            flash(f'Error: {e}')
            return redirect(url_for('accounts.accounts'))
        if is_htmx():
            with db_cursor() as cursor:
                account = _fetch_account_row(cursor, new_id)
            resp = make_response(render_template('partials/_account_row.html', account=account))
            return hx_toast(resp, 'Account added')
        flash('Account added successfully')
        return redirect(url_for('accounts.accounts'))

    with db_cursor() as cursor:
        cursor.execute(ACCOUNT_ROW_SQL.format(extra=""), (current_user.id,))
        all_accounts = cursor.fetchall()
    return render_template('accounts.html', accounts=all_accounts)


@bp.route('/accounts/<int:account_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_account(account_id):
    # Guard ownership up front (404 for missing/other-user).
    with db_cursor() as cursor:
        account = _fetch_account_row(cursor, account_id)

    if request.method == 'POST':
        name = request.form['name'].strip()
        account_type = request.form.get('type', '').strip()
        error = _validate(name, account_type)
        if error:
            account = (account[0], name, account_type, account[3], account[4], account[5])
            return render_template('partials/_account_edit_row.html', account=account, error=error)
        try:
            with db_cursor(commit=True) as cursor:
                cursor.execute(
                    "UPDATE account SET account_name=%s, type=%s WHERE account_id=%s AND user_id=%s",
                    (name, account_type, account_id, current_user.id),
                )
        except Exception as e:
            account = (account[0], name, account_type, account[3], account[4], account[5])
            return render_template('partials/_account_edit_row.html', account=account, error=str(e))
        with db_cursor() as cursor:
            account = _fetch_account_row(cursor, account_id)
        resp = make_response(render_template('partials/_account_row.html', account=account))
        return hx_toast(resp, 'Account updated')

    return render_template('partials/_account_edit_row.html', account=account)


@bp.route('/accounts/<int:account_id>/checkin', methods=['GET', 'POST'])
@login_required
def checkin_account(account_id):
    # Guard ownership up front (404 for missing/other-user).
    with db_cursor() as cursor:
        account = _fetch_account_row(cursor, account_id)

    if request.method == 'GET':
        return render_template('partials/_account_checkin_row.html', account=account)

    actual, error = parse_signed_amount(request.form.get('actual_balance'), 'Bank balance')
    if error:
        return render_template('partials/_account_checkin_row.html', account=account, error=error)

    today = date.today()
    with db_cursor(commit=True) as cursor:
        # Lock the account row: re-guards ownership inside the write transaction
        # AND serializes concurrent check-ins, so two tabs posting at once can't
        # both insert the gap (the due-runner FOR UPDATE lesson).
        cursor.execute(
            "SELECT 1 FROM account WHERE account_id = %s AND user_id = %s FOR UPDATE",
            (account_id, current_user.id),
        )
        if cursor.fetchone() is None:
            abort(404)
        # Recompute the balance inside the locked transaction — never trust a
        # posted "app balance".
        cursor.execute(
            "SELECT COALESCE(SUM(CASE WHEN transaction_type = 'income' "
            "THEN amount ELSE -amount END), 0) "
            "FROM transactions WHERE account_id = %s AND user_id = %s",
            (account_id, current_user.id),
        )
        computed = float(cursor.fetchone()[0])
        delta = round(actual - computed, 2)
        if abs(delta) >= 0.01:
            cursor.execute(
                "INSERT INTO transactions (amount, description, transaction_date, "
                "category_id, account_id, transaction_type, is_adjustment, user_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (abs(delta), 'Balance check-in', today, None, account_id,
                 'income' if delta > 0 else 'expense', True, current_user.id),
            )
        cursor.execute(
            "UPDATE account SET last_checked_in = %s WHERE account_id = %s AND user_id = %s",
            (today, account_id, current_user.id),
        )

    with db_cursor() as cursor:
        account = _fetch_account_row(cursor, account_id)
    resp = make_response(render_template('partials/_account_row.html', account=account))
    if abs(delta) >= 0.01:
        sign = '+' if delta > 0 else '-'
        return hx_toast(resp, f'Checked in — adjusted by {sign}${abs(delta):,.2f}')
    return hx_toast(resp, 'Checked in — balances match')


@bp.route('/accounts/<int:account_id>/row')
@login_required
def account_row(account_id):
    with db_cursor() as cursor:
        account = _fetch_account_row(cursor, account_id)
    return render_template('partials/_account_row.html', account=account)


@bp.route('/accounts/<int:account_id>', methods=['DELETE'])
@login_required
def delete_account(account_id):
    with db_cursor() as cursor:
        account = _fetch_account_row(cursor, account_id)
    try:
        with db_cursor(commit=True) as cursor:
            cursor.execute(
                "DELETE FROM account WHERE account_id = %s AND user_id = %s",
                (account_id, current_user.id),
            )
    except Exception as e:
        msg = ('Cannot delete — this account is used by existing transactions'
               if 'foreign key' in str(e).lower() else f'Error: {e}')
        resp = make_response(render_template('partials/_account_row.html', account=account))
        return hx_toast(resp, msg, 'error')
    return hx_toast(make_response('', 200), 'Account deleted')
