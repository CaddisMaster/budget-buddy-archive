from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort,
    make_response
)
from flask_login import login_required, current_user
from app.db import get_db_connection, db_cursor
from app.helpers import is_htmx, hx_toast

bp = Blueprint('accounts', __name__)

VALID_ACCOUNT_TYPES = ('Credit Card', 'Debit Card', 'Bank Account')

ACCOUNT_ROW_SQL = """
    SELECT a.account_id, a.account_name, a.type,
        COALESCE(SUM(CASE WHEN t.transaction_type = 'income'
            THEN t.amount ELSE -t.amount END), 0) AS balance
    FROM account a
    LEFT JOIN transactions t ON a.account_id = t.account_id AND t.user_id = a.user_id
    WHERE a.user_id = %s {extra}
    GROUP BY a.account_id, a.account_name, a.type
    ORDER BY a.account_name
"""


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
            account = (account[0], name, account_type, account[3])
            return render_template('partials/_account_edit_row.html', account=account, error=error)
        try:
            with db_cursor(commit=True) as cursor:
                cursor.execute(
                    "UPDATE account SET account_name=%s, type=%s WHERE account_id=%s AND user_id=%s",
                    (name, account_type, account_id, current_user.id),
                )
        except Exception as e:
            account = (account[0], name, account_type, account[3])
            return render_template('partials/_account_edit_row.html', account=account, error=str(e))
        with db_cursor() as cursor:
            account = _fetch_account_row(cursor, account_id)
        resp = make_response(render_template('partials/_account_row.html', account=account))
        return hx_toast(resp, 'Account updated')

    return render_template('partials/_account_edit_row.html', account=account)


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
