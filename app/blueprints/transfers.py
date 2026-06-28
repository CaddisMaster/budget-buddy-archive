from datetime import date, datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort,
    make_response
)
from flask_login import login_required, current_user
from app.db import get_db_connection, db_cursor
from app.helpers import hx_toast, is_htmx
from app.blueprints.transactions import (
    compute_next_due, VALID_FREQUENCIES, FREQUENCY_LABELS,
)
from app.blueprints.schedules import compute_initial_semimonthly_due

bp = Blueprint('transfers', __name__)

# Joined row for a recurring transfer (with the from/to account names) — the
# tuple shape the row partials read.
TRANSFER_SCHEDULE_ROW_SQL = """
    SELECT ts.id, ts.amount, ts.description, ts.from_account_id, ts.to_account_id,
           ts.frequency, ts.anchor_day, ts.second_day, ts.next_due, ts.is_active,
           fa.account_name, ta.account_name
    FROM transfer_schedules ts
    LEFT JOIN account fa ON ts.from_account_id = fa.account_id
    LEFT JOIN account ta ON ts.to_account_id = ta.account_id
    WHERE ts.user_id = %s {extra}
    ORDER BY ts.next_due, ts.id
"""


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
    # Post any due recurring transfers before rendering, so the History and
    # balances are current the moment the user lands on the tab.
    run_due_transfers(current_user.id)
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
    cursor.execute(TRANSFER_SCHEDULE_ROW_SQL.format(extra=""), (current_user.id,))
    schedules = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('transfer.html', accounts=all_accounts,
                           schedules=schedules, frequency_labels=FREQUENCY_LABELS)


@bp.route('/transfers/<int:group_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_transfer(group_id):
    # Imported here to avoid a circular import at module load.
    from app.blueprints.transactions import render_history_tbody
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM transactions WHERE transfer_group_id = %s AND user_id = %s LIMIT 1",
        (group_id, current_user.id),
    )
    if cursor.fetchone() is None:
        cursor.close(); conn.close()
        abort(404)

    all_accounts = _fetch_accounts(cursor, current_user.id)
    account_ids = {a[0] for a in all_accounts}

    if request.method == 'POST':
        fields, errors = _validate(request.form, account_ids)
        if errors:
            cursor.close(); conn.close()
            transfer = {
                'group_id': group_id,
                'amount': request.form.get('amount', ''),
                'description': request.form.get('description', ''),
                'transfer_date': fields['transfer_date'],
                'from_account': int(fields['from_account']) if fields['from_account'] else None,
                'to_account': int(fields['to_account']) if fields['to_account'] else None,
            }
            return render_template('partials/_transfer_edit_row.html',
                                   transfer=transfer, accounts=all_accounts,
                                   filter_qs=request.query_string.decode(),
                                   errors=errors)
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
        except Exception as e:
            conn.rollback()
            cursor.close(); conn.close()
            transfer = {
                'group_id': group_id,
                'amount': request.form.get('amount', ''),
                'description': request.form.get('description', ''),
                'transfer_date': fields['transfer_date'],
                'from_account': int(fields['from_account']) if fields['from_account'] else None,
                'to_account': int(fields['to_account']) if fields['to_account'] else None,
            }
            return render_template('partials/_transfer_edit_row.html',
                                   transfer=transfer, accounts=all_accounts,
                                   filter_qs=request.query_string.decode(),
                                   errors=[str(e)])
        cursor.close(); conn.close()
        return hx_toast(make_response(render_history_tbody()), 'Transfer updated')

    # Pull the current pair to prefill the form.
    cursor.execute(
        "SELECT amount, description, transaction_date, account_id, transaction_type "
        "FROM transactions WHERE transfer_group_id = %s AND user_id = %s",
        (group_id, current_user.id),
    )
    legs = cursor.fetchall()
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
    return render_template('partials/_transfer_edit_row.html',
                           transfer=transfer, accounts=all_accounts,
                           filter_qs=request.query_string.decode())


@bp.route('/transfers/<int:group_id>', methods=['DELETE'])
@login_required
def delete_transfer(group_id):
    from app.blueprints.transactions import render_history_tbody
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM transactions WHERE transfer_group_id = %s AND user_id = %s LIMIT 1",
        (group_id, current_user.id),
    )
    if cursor.fetchone() is None:
        cursor.close(); conn.close()
        abort(404)
    try:
        cursor.execute(
            "DELETE FROM transactions WHERE transfer_group_id = %s AND user_id = %s",
            (group_id, current_user.id),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        cursor.close(); conn.close()
        return hx_toast(make_response(render_history_tbody()), f'Error: {e}', 'error')
    cursor.close()
    conn.close()
    return hx_toast(make_response(render_history_tbody()), 'Transfer deleted')


# ---------------------------------------------------------------------------
# v10.4 Auto-Transfer — recurring transfers (the transfer-tab twin of Scheduled).
# A transfer_schedules row materializes a PAIRED transfer (linked expense+income
# legs sharing one transfer_group_id, is_transfer=true) on each due date going
# forward. Reuses the schedules.py date math + the paired-leg INSERT above.
# ---------------------------------------------------------------------------

def run_due_transfers(user_id):
    """Materialize a paired transfer for every active recurring transfer whose
    next_due has arrived, catching up (loop) if several occurrences are overdue,
    then advance next_due past today. Scoped to the given user. The twin of
    run_due_schedules()."""
    today = date.today()
    with db_cursor(commit=True) as cursor:
        cursor.execute("""
            SELECT id, amount, description, from_account_id, to_account_id,
                   frequency, anchor_day, second_day, next_due
            FROM transfer_schedules
            WHERE is_active = true AND next_due <= %s AND user_id = %s
        """, (today, user_id))
        due = cursor.fetchall()
        for (tsid, amount, desc, from_acc, to_acc, freq,
             anchor_day, second_day, next_due) in due:
            label = desc or 'Transfer'
            cur_due = next_due
            while cur_due <= today:
                cursor.execute("SELECT nextval('transfer_group_seq')")
                gid = cursor.fetchone()[0]
                # Expense leg out of the From account.
                cursor.execute(
                    "INSERT INTO transactions (amount, description, transaction_date, "
                    "account_id, transaction_type, is_transfer, transfer_group_id, user_id) "
                    "VALUES (%s, %s, %s, %s, 'expense', true, %s, %s)",
                    (amount, label, cur_due, from_acc, gid, user_id))
                # Income leg into the To account.
                cursor.execute(
                    "INSERT INTO transactions (amount, description, transaction_date, "
                    "account_id, transaction_type, is_transfer, transfer_group_id, user_id) "
                    "VALUES (%s, %s, %s, %s, 'income', true, %s, %s)",
                    (amount, label, cur_due, to_acc, gid, user_id))
                cur_due = compute_next_due(
                    cur_due, freq, anchor_day=anchor_day, second_day=second_day)
            cursor.execute(
                "UPDATE transfer_schedules SET next_due = %s WHERE id = %s AND user_id = %s",
                (cur_due, tsid, user_id))


def _parse_transfer_schedule_form(form, user_account_ids):
    """Validate a recurring-transfer create/edit form. Returns (errors, fields)
    with the computed next_due/anchor_day/second_day ready to store. No category
    (transfers are category-less); both accounts must be the user's own."""
    errors = []
    from_account = form.get('from_account') or None
    to_account = form.get('to_account') or None
    description = form.get('description', '').strip()

    amount = None
    amount_str = form.get('amount', '').strip()
    if not amount_str:
        errors.append('Amount is required')
    else:
        try:
            amount = float(amount_str)
            if amount <= 0:
                errors.append('Amount must be greater than zero')
        except ValueError:
            errors.append('Amount must be a valid number')

    if not from_account or not to_account:
        errors.append('Both a From and a To account are required')
    elif from_account == to_account:
        errors.append('From and To accounts must be different')
    elif (int(from_account) not in user_account_ids
          or int(to_account) not in user_account_ids):
        errors.append('Invalid account')

    frequency = form.get('frequency')
    if frequency not in VALID_FREQUENCIES:
        errors.append('Please choose a valid frequency')

    anchor_day = second_day = None
    next_due = None
    today = date.today()
    if frequency == 'semimonthly':
        try:
            anchor_day = int(form.get('anchor_day', '').strip())
            second_day = int(form.get('second_day', '').strip())
            if not (1 <= anchor_day <= 31 and 1 <= second_day <= 31):
                errors.append('Days must be between 1 and 31')
            elif anchor_day == second_day:
                errors.append('The two days must be different')
            else:
                next_due = compute_initial_semimonthly_due(anchor_day, second_day, today)
        except ValueError:
            errors.append('Both days are required for a semi-monthly transfer')
    elif frequency in VALID_FREQUENCIES:
        next_date_raw = form.get('next_due', '').strip()
        if not next_date_raw:
            errors.append('Next date is required')
        else:
            try:
                next_due = datetime.strptime(next_date_raw, '%Y-%m-%d').date()
                if next_due < today:
                    errors.append('Next date cannot be in the past')
            except ValueError:
                errors.append('Next date must be a valid date')

    fields = dict(amount=amount, description=description,
                  from_account=from_account, to_account=to_account,
                  frequency=frequency, anchor_day=anchor_day,
                  second_day=second_day, next_due=next_due)
    return errors, fields


def _fetch_transfer_schedule_row(cursor, schedule_id):
    """Single recurring-transfer row (joined account names) for the current user,
    or 404."""
    cursor.execute(TRANSFER_SCHEDULE_ROW_SQL.format(extra="AND ts.id = %s"),
                   (current_user.id, schedule_id))
    row = cursor.fetchone()
    if row is None:
        abort(404)
    return row


@bp.route('/transfers/recurring', methods=['POST'])
@login_required
def create_transfer_schedule():
    with db_cursor() as cursor:
        account_ids = {a[0] for a in _fetch_accounts(cursor, current_user.id)}
    errors, f = _parse_transfer_schedule_form(request.form, account_ids)
    if errors:
        msg = errors[0]
        if is_htmx():
            return hx_toast(make_response('', 200), msg, 'error')
        flash(msg)
        return redirect(url_for('transfers.transfers'))
    try:
        with db_cursor(commit=True) as cursor:
            cursor.execute("""
                INSERT INTO transfer_schedules
                    (amount, description, from_account_id, to_account_id,
                     frequency, anchor_day, second_day, next_due, user_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (f['amount'], f['description'], f['from_account'], f['to_account'],
                  f['frequency'], f['anchor_day'], f['second_day'], f['next_due'],
                  current_user.id))
            new_id = cursor.fetchone()[0]
    except Exception as e:
        if is_htmx():
            return hx_toast(make_response('', 200), f'Error: {e}', 'error')
        flash(f'Error: {e}')
        return redirect(url_for('transfers.transfers'))
    if is_htmx():
        with db_cursor() as cursor:
            schedule = _fetch_transfer_schedule_row(cursor, new_id)
        resp = make_response(render_template(
            'partials/_transfer_schedule_row.html', schedule=schedule,
            frequency_labels=FREQUENCY_LABELS))
        return hx_toast(resp, 'Automatic transfer added')
    flash('Automatic transfer added')
    return redirect(url_for('transfers.transfers'))


@bp.route('/transfers/recurring/<int:schedule_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_transfer_schedule(schedule_id):
    with db_cursor() as cursor:
        schedule = _fetch_transfer_schedule_row(cursor, schedule_id)
        accounts = _fetch_accounts(cursor, current_user.id)
        account_ids = {a[0] for a in accounts}

    if request.method == 'POST':
        errors, f = _parse_transfer_schedule_form(request.form, account_ids)
        if errors:
            return render_template('partials/_transfer_schedule_edit_row.html',
                                   schedule=schedule, accounts=accounts,
                                   error=errors[0])
        try:
            with db_cursor(commit=True) as cursor:
                cursor.execute("""
                    UPDATE transfer_schedules SET amount=%s, description=%s,
                        from_account_id=%s, to_account_id=%s, frequency=%s,
                        anchor_day=%s, second_day=%s, next_due=%s
                    WHERE id=%s AND user_id=%s
                """, (f['amount'], f['description'], f['from_account'],
                      f['to_account'], f['frequency'], f['anchor_day'],
                      f['second_day'], f['next_due'], schedule_id, current_user.id))
        except Exception as e:
            return render_template('partials/_transfer_schedule_edit_row.html',
                                   schedule=schedule, accounts=accounts, error=str(e))
        with db_cursor() as cursor:
            schedule = _fetch_transfer_schedule_row(cursor, schedule_id)
        resp = make_response(render_template(
            'partials/_transfer_schedule_row.html', schedule=schedule,
            frequency_labels=FREQUENCY_LABELS))
        return hx_toast(resp, 'Automatic transfer updated')

    return render_template('partials/_transfer_schedule_edit_row.html',
                           schedule=schedule, accounts=accounts)


@bp.route('/transfers/recurring/<int:schedule_id>/row')
@login_required
def transfer_schedule_row(schedule_id):
    with db_cursor() as cursor:
        schedule = _fetch_transfer_schedule_row(cursor, schedule_id)
    return render_template('partials/_transfer_schedule_row.html',
                           schedule=schedule, frequency_labels=FREQUENCY_LABELS)


@bp.route('/transfers/recurring/<int:schedule_id>', methods=['DELETE'])
@login_required
def delete_transfer_schedule(schedule_id):
    with db_cursor() as cursor:
        _fetch_transfer_schedule_row(cursor, schedule_id)
    with db_cursor(commit=True) as cursor:
        cursor.execute("DELETE FROM transfer_schedules WHERE id = %s AND user_id = %s",
                       (schedule_id, current_user.id))
    return hx_toast(make_response('', 200), 'Automatic transfer deleted')
