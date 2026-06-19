import os
import subprocess
from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, abort
from flask_login import login_required, current_user
from app import bcrypt
from app.db import get_db_connection
from app.helpers import is_htmx, hx_toast

bp = Blueprint('admin', __name__)


@bp.route('/admin/create-user', methods=['GET', 'POST'])
@login_required
def create_user():
    if not current_user.is_admin:
        flash('Access denied')
        return redirect(url_for('main.index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        is_admin = request.form.get('is_admin') == 'on'
        errors = []
        if not username:
            errors.append('Username is required')
        elif len(username) < 3:
            errors.append('Username must be at least 3 characters')
        elif len(username) > 50:
            errors.append('Username must be 50 characters or fewer')
        if not password:
            errors.append('Password is required')
        elif len(password) < 8:
            errors.append('Password must be at least 8 characters')
        if errors:
            for e in errors:
                flash(e)
            return redirect(url_for('admin.create_user'))
        password_hash = bcrypt.generate_password_hash(password).decode('utf-8')
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO users (username, password_hash, is_admin) VALUES (%s, %s, %s) RETURNING id",
                (username, password_hash, is_admin)
            )
            new_user_id = cursor.fetchone()[0]
            default_categories = [
                ('Housing', 'Rent, mortgage, utilities'),
                ('Food & Dining', 'Groceries, restaurants'),
                ('Transportation', 'Gas, public transit, car maintenance'),
                ('Healthcare', 'Doctor, pharmacy, insurance'),
                ('Entertainment', 'Movies, subscriptions, hobbies'),
                ('Shopping', 'Clothing, electronics, household'),
                ('Personal Care', 'Haircuts, gym, personal products'),
                ('Income', 'Salary, freelance, other income'),
                ('Other', 'Miscellaneous expenses'),
            ]
            for cat_name, cat_desc in default_categories:
                cursor.execute(
                    "INSERT INTO categories (name, description, user_id) VALUES (%s, %s, %s)",
                    (cat_name, cat_desc, new_user_id)
                )
            conn.commit()
            flash(f'Account created for {username}')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        finally:
            cursor.close()
            conn.close()
        return redirect(url_for('admin.create_user'))
    return render_template('create_user.html')


@bp.route('/admin/backup')
@login_required
def backup_database():
    if not current_user.is_admin:
        flash('Access denied')
        return redirect(url_for('main.index'))
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
    filename = f'budget_backup_{date.today()}.sql'
    response = make_response(result.stdout)
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    response.headers['Content-Type'] = 'application/octet-stream'
    return response


@bp.route('/settings')
@login_required
def settings():
    if not current_user.is_admin:
        flash('Access denied')
        return redirect(url_for('main.index'))
    return render_template('settings.html')


@bp.route('/admin/users')
@login_required
def admin_users():
    if not current_user.is_admin:
        flash('Access denied')
        return redirect(url_for('main.index'))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, is_admin, created_at FROM users ORDER BY created_at")
    users = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('admin_users.html', users=users)


@bp.route('/admin/users/<int:user_id>', methods=['DELETE'])
@login_required
def delete_user(user_id):
    if not current_user.is_admin:
        abort(403)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, is_admin, created_at FROM users WHERE id = %s", (user_id,))
    user = cursor.fetchone()
    if not user:
        cursor.close(); conn.close()
        abort(404)
    if user_id == current_user.id:
        cursor.close(); conn.close()
        resp = make_response(render_template('partials/_user_row.html', u=user))
        return hx_toast(resp, 'You cannot delete your own account', 'error')
    try:
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        cursor.close(); conn.close()
        resp = make_response(render_template('partials/_user_row.html', u=user))
        return hx_toast(resp, f'Error: {e}', 'error')
    cursor.close()
    conn.close()
    return hx_toast(make_response('', 200), f'User {user[1]} deleted')


@bp.route('/admin/users/toggle-admin/<int:user_id>', methods=['POST'])
@login_required
def toggle_admin(user_id):
    if not current_user.is_admin:
        abort(403)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, is_admin, created_at FROM users WHERE id = %s", (user_id,))
    user = cursor.fetchone()
    if not user:
        cursor.close(); conn.close()
        abort(404)
    if user_id == current_user.id:
        cursor.close(); conn.close()
        resp = make_response(render_template('partials/_user_row.html', u=user))
        return hx_toast(resp, 'You cannot change your own admin status', 'error')
    cursor.execute("UPDATE users SET is_admin = NOT is_admin WHERE id = %s", (user_id,))
    conn.commit()
    cursor.execute("SELECT id, username, is_admin, created_at FROM users WHERE id = %s", (user_id,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()
    resp = make_response(render_template('partials/_user_row.html', u=user))
    return hx_toast(resp, 'Admin status updated')
