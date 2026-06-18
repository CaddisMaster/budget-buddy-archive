from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort
)
from flask_login import login_required, current_user
from app.db import get_db_connection

bp = Blueprint('transfers', __name__)


def _fetch_accounts(cursor, user_id):
    """The account dropdown for the From/To selects — same shape as the
    transaction form uses."""
    cursor.execute(
        "SELECT account_id, account_name FROM account WHERE user_id = %s ORDER BY account_name",
        (user_id,),
    )
    return cursor.fetchall()


def _validate(form, user_account_ids):
    """Shared validation for create + edit. Returns (fields, errors)."""
    errors = []
    from_account = form.get('from_account') or None
    to_account = form.get('to_account') or None
    amount_str = form.get('amount', '').strip()
    transfer_date = form.get('transfer_date', '').strip()
    description = form.get('description', '').strip()

    amount = None
    if not amount_str:
        errors.append('Amount is required')
    else:
        try:
            amount = float(amount_str)
            if amount <= 0:
                errors.append('Amount must be greater than zero')
        except ValueError:
            errors.append('Amount must be a valid number')
    if not transfer_date:
        errors.append('Date is required')
    if not from_account or not to_account:
        errors.append('Both a From and a To account are required')
    elif from_account == to_account:
        errors.append('From and To accounts must be different')
    elif (int(from_account) not in user_account_ids
          or int(to_account) not in user_account_ids):
        errors.append('Invalid account')

    fields = {
        'from_account': from_account,
        'to_account': to_account,
        'amount': amount,
        'transfer_date': transfer_date,
        'description': description,
    }
    return fields, errors


@bp.route('/transfers', methods=['GET', 'POST'])
@login_required
def transfers():
    conn = get_db_connection()
    cursor = conn.cursor()
    account_ids = {a[0] for a in _fetch_accounts(cursor, current_user.id)}

    if request.method == 'POST':
        fields, errors = _validate(request.form, account_ids)
        if errors:
            cursor.close(); conn.close()
            for e in errors:
                flash(e)
            return redirect(url_for('transfers.transfers'))
        desc = fields['description'] or 'Transfer'
        try:
            cursor.execute("SELECT nextval('transfer_group_seq')")
            gid = cursor.fetchone()[0]
            # Expense leg out of the From account.
            cursor.execute(
                "INSERT INTO transactions (amount, description, transaction_date, "
                "account_id, transaction_type, is_transfer, transfer_group_id, user_id) "
                "VALUES (%s, %s, %s, %s, 'expense', true, %s, %s)",
                (fields['amount'], desc, fields['transfer_date'],
                 fields['from_account'], gid, current_user.id),
            )
            # Income leg into the To account.
            cursor.execute(
                "INSERT INTO transactions (amount, description, transaction_date, "
                "account_id, transaction_type, is_transfer, transfer_group_id, user_id) "
                "VALUES (%s, %s, %s, %s, 'income', true, %s, %s)",
                (fields['amount'], desc, fields['transfer_date'],
                 fields['to_account'], gid, current_user.id),
            )
            conn.commit()
            flash('Transfer added successfully')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect(url_for('transfers.transfers'))

    all_accounts = _fetch_accounts(cursor, current_user.id)
    cursor.close()
    conn.close()
    return render_template('transfer.html', accounts=all_accounts)


@bp.route('/transfers/edit/<int:group_id>', methods=['GET', 'POST'])
@login_required
def edit_transfer(group_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM transactions WHERE transfer_group_id = %s AND user_id = %s LIMIT 1",
        (group_id, current_user.id),
    )
    if cursor.fetchone() is None:
        cursor.close(); conn.close()
        abort(404)

    account_ids = {a[0] for a in _fetch_accounts(cursor, current_user.id)}

    if request.method == 'POST':
        fields, errors = _validate(request.form, account_ids)
        if errors:
            cursor.close(); conn.close()
            for e in errors:
                flash(e)
            return redirect(url_for('transfers.edit_transfer', group_id=group_id))
        desc = fields['description'] or 'Transfer'
        try:
            # Expense leg → From account; income leg → To account. Updating by
            # transaction_type keeps each leg pointed at the right account.
            cursor.execute(
                "UPDATE transactions SET amount=%s, description=%s, transaction_date=%s, "
                "account_id=%s WHERE transfer_group_id=%s AND transaction_type='expense' "
                "AND user_id=%s",
                (fields['amount'], desc, fields['transfer_date'],
                 fields['from_account'], group_id, current_user.id),
            )
            cursor.execute(
                "UPDATE transactions SET amount=%s, description=%s, transaction_date=%s, "
                "account_id=%s WHERE transfer_group_id=%s AND transaction_type='income' "
                "AND user_id=%s",
                (fields['amount'], desc, fields['transfer_date'],
                 fields['to_account'], group_id, current_user.id),
            )
            conn.commit()
            flash('Transfer updated successfully')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect('/transactions')

    # Pull the current pair to prefill the form.
    cursor.execute(
        "SELECT amount, description, transaction_date, account_id, transaction_type "
        "FROM transactions WHERE transfer_group_id = %s AND user_id = %s",
        (group_id, current_user.id),
    )
    legs = cursor.fetchall()
    all_accounts = _fetch_accounts(cursor, current_user.id)
    cursor.close()
    conn.close()
    transfer = {
        'group_id': group_id,
        'amount': legs[0][0],
        'description': legs[0][1],
        'transfer_date': legs[0][2],
        'from_account': next((l[3] for l in legs if l[4] == 'expense'), None),
        'to_account': next((l[3] for l in legs if l[4] == 'income'), None),
    }
    return render_template('edit_transfer.html', transfer=transfer, accounts=all_accounts)


@bp.route('/transfers/delete/<int:group_id>', methods=['GET', 'POST'])
@login_required
def delete_transfer(group_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM transactions WHERE transfer_group_id = %s AND user_id = %s LIMIT 1",
        (group_id, current_user.id),
    )
    if cursor.fetchone() is None:
        cursor.close(); conn.close()
        abort(404)
    if request.method == 'POST':
        try:
            cursor.execute(
                "DELETE FROM transactions WHERE transfer_group_id = %s AND user_id = %s",
                (group_id, current_user.id),
            )
            conn.commit()
            flash('Transfer deleted')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect('/transactions')
    cursor.execute(
        "SELECT amount, description, transaction_date, account_id, transaction_type "
        "FROM transactions WHERE transfer_group_id = %s AND user_id = %s",
        (group_id, current_user.id),
    )
    legs = cursor.fetchall()
    # Resolve account names for the confirm screen.
    cursor.execute(
        "SELECT account_id, account_name FROM account WHERE user_id = %s",
        (current_user.id,),
    )
    names = {a[0]: a[1] for a in cursor.fetchall()}
    cursor.close()
    conn.close()
    transfer = {
        'group_id': group_id,
        'amount': legs[0][0],
        'description': legs[0][1],
        'transfer_date': legs[0][2],
        'from_name': names.get(next((l[3] for l in legs if l[4] == 'expense'), None)),
        'to_name': names.get(next((l[3] for l in legs if l[4] == 'income'), None)),
    }
    return render_template('delete_transfer.html', transfer=transfer)
