import re

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, login_required, current_user
from app import bcrypt, limiter
from app.db import db_cursor
from app.mailer import mail_enabled
from app.models import User

bp = Blueprint('auth', __name__)

# Deliberately loose — just enough to reject obvious typos ("no @" / "no dot");
# real validity is proven by whether Resend can deliver.
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

# A throwaway bcrypt hash checked when the username doesn't exist, so a missing
# user costs the same bcrypt work as a wrong password — no username enumeration
# via response timing (v10.1.1).
_DUMMY_PASSWORD_HASH = bcrypt.generate_password_hash('dummy-password-never-matches').decode('utf-8')


@bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.get_by_username(username)
        # Always run exactly one bcrypt check (dummy hash when the user is
        # missing) to keep the response time constant either way.
        password_hash = user.password_hash if user else _DUMMY_PASSWORD_HASH
        password_ok = bcrypt.check_password_hash(password_hash, password)
        if user and password_ok:
            session.clear()  # drop any pre-login session state (fixation hygiene)
            # remember=True (v10.13): the installed-PWA case — without the
            # remember cookie a standalone app re-prompts login every launch.
            # The REMEMBER_COOKIE_* flags were already configured in __init__.py.
            login_user(user, remember=True)
            return redirect(url_for('main.index'))
        flash('Invalid username or password')
    return render_template('login.html')


@bp.route('/logout', methods=['POST'])
@login_required
def logout():
    # POST-only: a GET logout is CSRF-able (any <img src="/logout"> logs the
    # user out). The nav renders a tiny form; CSRFProtect covers the POST.
    logout_user()
    return redirect(url_for('auth.login'))


@bp.route('/profile')
@login_required
def profile():
    with db_cursor() as cursor:
        cursor.execute("SELECT created_at, email, weekly_digest FROM users WHERE id = %s",
                       (current_user.id,))
        row = cursor.fetchone()
        created_at = row[0] if row else None
        email = row[1] if row else None
        weekly_digest = row[2] if row else False
        # A small at-a-glance summary of the user's data.
        cursor.execute("SELECT COUNT(*) FROM transactions WHERE user_id = %s", (current_user.id,))
        txn_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM categories WHERE user_id = %s", (current_user.id,))
        cat_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM account WHERE user_id = %s", (current_user.id,))
        acct_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM goals WHERE user_id = %s", (current_user.id,))
        goal_count = cursor.fetchone()[0]
    return render_template('profile.html', created_at=created_at,
                           txn_count=txn_count, cat_count=cat_count,
                           acct_count=acct_count, goal_count=goal_count,
                           email=email, weekly_digest=weekly_digest,
                           mail_enabled=mail_enabled())


@bp.route('/profile/settings', methods=['POST'])
@login_required
def profile_settings():
    """Save the weekly-digest opt-in + email address. Scoped to current_user, so
    a user can only ever change their own row."""
    email = request.form.get('email', '').strip()
    weekly_digest = request.form.get('weekly_digest') == 'on'

    if email and not _EMAIL_RE.match(email):
        flash('Please enter a valid email address')
        return redirect(url_for('auth.profile'))
    if weekly_digest and not email:
        flash('Add an email address to receive the weekly digest')
        return redirect(url_for('auth.profile'))

    with db_cursor(commit=True) as cursor:
        cursor.execute(
            "UPDATE users SET email = %s, weekly_digest = %s WHERE id = %s",
            (email or None, weekly_digest, current_user.id))
    flash('Preferences saved')
    return redirect(url_for('auth.profile'))


@bp.route('/change-password', methods=['GET', 'POST'])
@limiter.limit("10 per minute", methods=["POST"])
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
        if len(new_password.encode('utf-8')) > 72:
            # bcrypt silently truncates beyond 72 BYTES — reject instead.
            flash('New password must be 72 bytes or fewer')
            return redirect(url_for('auth.change_password'))
        new_hash = bcrypt.generate_password_hash(new_password).decode('utf-8')
        with db_cursor(commit=True) as cursor:
            cursor.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (new_hash, current_user.id)
            )
        flash('Password updated')
        return redirect(url_for('main.index'))
    return render_template('change_password.html')
