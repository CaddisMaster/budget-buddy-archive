from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from app import bcrypt
from app.db import get_db_connection
from app.models import User

bp = Blueprint('auth', __name__)


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.get_by_username(username)
        if user and bcrypt.check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('main.index'))
        flash('Invalid username or password')
    return render_template('login.html')


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))


@bp.route('/profile')
@login_required
def profile():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT created_at FROM users WHERE id = %s", (current_user.id,))
    row = cursor.fetchone()
    created_at = row[0] if row else None
    # A small at-a-glance summary of the user's data.
    cursor.execute("SELECT COUNT(*) FROM transactions WHERE user_id = %s", (current_user.id,))
    txn_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM categories WHERE user_id = %s", (current_user.id,))
    cat_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM account WHERE user_id = %s", (current_user.id,))
    acct_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM goals WHERE user_id = %s", (current_user.id,))
    goal_count = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return render_template('profile.html', created_at=created_at,
                           txn_count=txn_count, cat_count=cat_count,
                           acct_count=acct_count, goal_count=goal_count)


@bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current_password = request.form.get('current_password', '')
        new_password = request.form.get('new_password', '')
        if not bcrypt.check_password_hash(current_user.password_hash, current_password):
            flash('Current password is incorrect')
            return redirect(url_for('auth.change_password'))
        if len(new_password) < 8:
            flash('New password must be at least 8 characters')
            return redirect(url_for('auth.change_password'))
        new_hash = bcrypt.generate_password_hash(new_password).decode('utf-8')
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (new_hash, current_user.id)
        )
        conn.commit()
        cursor.close()
        conn.close()
        flash('Password updated')
        return redirect(url_for('main.index'))
    return render_template('change_password.html')
