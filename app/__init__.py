import os
import hmac
from flask import Flask, redirect, url_for
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_bcrypt import Bcrypt
from flask_wtf.csrf import CSRFProtect

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')

# v10.1.1 hardening. Secure cookies + HSTS are gated on COOKIE_SECURE=1 (set in
# the Droplet .env) so local HTTP dev and the Werkzeug test client — both plain
# http — still set and send the session cookie.
_secure_cookies = os.getenv('COOKIE_SECURE', '') == '1'
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=_secure_cookies,
    REMEMBER_COOKIE_HTTPONLY=True,
    REMEMBER_COOKIE_SAMESITE='Lax',
    REMEMBER_COOKIE_SECURE=_secure_cookies,
)


@app.after_request
def set_security_headers(response):
    """Defense-in-depth response headers (v10.1.1). CSP is frame-ancestors only
    — a full policy would break HTMX/Chart.js/inline styles. HSTS is sent only
    in prod (COOKIE_SECURE=1), where TLS is terminated at Nginx."""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "frame-ancestors 'none'"
    response.headers['Referrer-Policy'] = 'no-referrer'
    if _secure_cookies:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response


csrf = CSRFProtect(app)

limiter = Limiter(
  get_remote_address,
  app=app,
  default_limits=["60 per minute"],
  storage_uri="memory://"
)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'

bcrypt = Bcrypt(app)

from app.models import User

@login_manager.user_loader
def load_user(user_id):
  return User.get_by_id(int(user_id))

from app.blueprints import (
  auth, main, transactions, categories, accounts, budgets, analytics, admin,
  transfers, goals, schedules, insights, forecasts, ask, digests, agent
)

app.register_blueprint(auth.bp)
app.register_blueprint(main.bp)
app.register_blueprint(transactions.bp)
app.register_blueprint(categories.bp)
app.register_blueprint(accounts.bp)
app.register_blueprint(budgets.bp)
app.register_blueprint(analytics.bp)
app.register_blueprint(admin.bp)
app.register_blueprint(transfers.bp)
app.register_blueprint(goals.bp)
app.register_blueprint(schedules.bp)
app.register_blueprint(insights.bp)
app.register_blueprint(forecasts.bp)
app.register_blueprint(ask.bp)
app.register_blueprint(digests.bp)
app.register_blueprint(agent.bp)

# `flask send-digests` — run the weekly digest send manually (local/testing).
app.cli.add_command(digests.send_digests_command)

# v10.8 Weekly Email Digest — in-process scheduler. Guarded on ENABLE_DIGEST_SCHEDULER
# so it fires ONLY in prod (never under pytest or local unless deliberately enabled),
# and on mail_enabled() so it's a no-op without a Resend key. gunicorn runs a single
# worker (no --preload), so exactly one scheduler instance exists — no double-fire.
# The users.last_digest_sent_on guard + misfire_grace_time make it safe across
# restarts. Sunday 18:00 America/New_York.
from app.mailer import mail_enabled

if os.getenv('ENABLE_DIGEST_SCHEDULER') == '1' and mail_enabled():
    from apscheduler.schedulers.background import BackgroundScheduler
    from app.blueprints.digests import send_weekly_digests

    _scheduler = BackgroundScheduler(timezone='America/New_York', daemon=True)
    _scheduler.add_job(send_weekly_digests, 'cron', day_of_week='sun', hour=18,
                       id='weekly_digest', replace_existing=True,
                       misfire_grace_time=3600)
    _scheduler.start()