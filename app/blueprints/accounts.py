from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.db import get_db_connection

bp = Blueprint('accounts', __name__)


@bp.route('/accounts', methods=['GET', 'POST'])
@login_required
def accounts():
    conn = get_db_connection()
    cursor = conn.cursor()
    valid_account_types = ('Credit Card', 'Debit Card', 'Bank Account')
    if request.method == 'POST':
        name = request.form['name'].strip()
        account_type = request.form.get('type', '').strip()
        if not name:
            cursor.close(); conn.close()
            flash('Name is required')
            return redirect(url_for('accounts.accounts'))
        if len(name) > 50:
            cursor.close(); conn.close()
            flash('Name must be 50 characters or fewer')
            return redirect(url_for('accounts.accounts'))
        if account_type not in valid_account_types:
            cursor.close(); conn.close()
            flash('Invalid account type')
            return redirect(url_for('accounts.accounts'))
        try:
            cursor.execute(
                "INSERT INTO account (account_name, type, user_id) VALUES (%s, %s, %s)",
                (name, account_type, current_user.id)
            )
            conn.commit()
            flash('Account added successfully')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect(url_for('accounts.accounts'))
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
        LEFT JOIN transactions t ON a.account_id = t.account_id AND t.user_id = a.user_id
        WHERE a.user_id = %s
        GROUP BY a.account_id, a.account_name, a.type
        ORDER BY a.account_name
    """, (current_user.id,))
    all_accounts = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('accounts.html', accounts=all_accounts)


@bp.route('/accounts/edit/<int:account_id>', methods=['GET', 'POST'])
@login_required
def edit_account(account_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        name = request.form['name'].strip()
        account_type = request.form.get('type', '').strip()
        if not name:
            cursor.close(); conn.close()
            flash('Name is required')
            return redirect(url_for('accounts.edit_account', account_id=account_id))
        if len(name) > 50:
            cursor.close(); conn.close()
            flash('Name must be 50 characters or fewer')
            return redirect(url_for('accounts.edit_account', account_id=account_id))
        if account_type not in ('Credit Card', 'Debit Card', 'Bank Account'):
            cursor.close(); conn.close()
            flash('Invalid account type')
            return redirect(url_for('accounts.edit_account', account_id=account_id))
        try:
            cursor.execute(
                "UPDATE account SET account_name=%s, type=%s WHERE account_id=%s AND user_id=%s",
                (name, account_type, account_id, current_user.id)
            )
            conn.commit()
            flash('Account updated successfully')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect(url_for('accounts.accounts'))
    cursor.execute("SELECT account_id, account_name, type FROM account WHERE account_id = %s AND user_id = %s", (account_id, current_user.id))
    account = cursor.fetchone()
    cursor.close()
    conn.close()
    return render_template('edit_account.html', account=account)


@bp.route('/accounts/delete/<int:account_id>', methods=['GET', 'POST'])
@login_required
def delete_account(account_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        try:
            cursor.execute("DELETE FROM account WHERE account_id = %s AND user_id = %s", (account_id, current_user.id))
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
        return redirect(url_for('accounts.accounts'))
    cursor.execute("SELECT account_id, account_name, type FROM account WHERE account_id = %s AND user_id = %s", (account_id, current_user.id))
    account = cursor.fetchone()
    cursor.close()
    conn.close()
    return render_template('delete_account.html', account=account)
