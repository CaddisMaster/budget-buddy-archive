"""v10.8 — Weekly Email Digest (Budget Buddy's first PUSH feature).

A scheduled Sunday-evening email that recaps the month-to-date (spend vs budget,
net position) and the week ahead (scheduled items in the next 7 days). Same split
of responsibility as the dashboard AI cards — the app computes every figure
deterministically (compute_digest_facts, reusing insights' compute_month_facts),
the model narrates once (ai.generate_digest) — only here it's delivered by email
(app.mailer) instead of rendered on a page.

The runner send_weekly_digests() is triggered two ways: the guarded APScheduler
job in app/__init__.py (prod), and the `flask send-digests` CLI (manual/local
testing). It is idempotent — a given week goes out at most once per user, guarded
by users.last_digest_sent_on — and per-user failures are isolated so one bad send
never aborts the batch. Opt-in + email address are set on the Profile page.
"""
import os
from datetime import date, timedelta

import click
from flask import Blueprint, render_template
from flask.cli import with_appcontext

from app.db import db_cursor
from app.ai import generate_digest, ParseError
from app import mailer
from app.helpers import most_recent_sunday
from app.blueprints.insights import compute_month_facts
from app.blueprints.agent import load_agent_run, run_money_agent

bp = Blueprint('digests', __name__)

DIGEST_SUBJECT = 'Your Budget Buddy weekly digest'
UPCOMING_DAYS = 7

_MONTHS = ('', 'January', 'February', 'March', 'April', 'May', 'June', 'July',
           'August', 'September', 'October', 'November', 'December')


def _upcoming_scheduled(user_id, today, days=UPCOMING_DAYS):
    """Every scheduled income/expense (schedules) and transfer
    (transfer_schedules) occurrence falling in [today, today+days] — the
    bills/paychecks/transfers coming up this week. Enumerates occurrences with
    the main.py occurrence walker rather than reading next_due once, so a weekly
    bill landing twice inside the window is counted twice. Projection only (no
    materialization). User-scoped. Returns [{description, amount, type, due},
    ...] sorted by due; type is one of 'income' | 'expense' | 'transfer'."""
    from app.blueprints.main import upcoming_occurrences  # lazy: avoids import cycle
    window_end = today + timedelta(days=days)
    # The walker's window is start-EXCLUSIVE; the digest window includes today.
    window_start = today - timedelta(days=1)
    items = []
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT description, amount, transaction_type, next_due,
                   frequency, anchor_day, second_day
            FROM schedules
            WHERE is_active = true AND user_id = %s AND next_due <= %s
        """, (user_id, window_end))
        for desc, amount, ttype, next_due, freq, anchor, second in cursor.fetchall():
            for due in upcoming_occurrences(next_due, freq, anchor, second,
                                            window_start, window_end):
                items.append({'description': desc or '', 'amount': float(amount),
                              'type': ttype, 'due': due.isoformat()})

        cursor.execute("""
            SELECT ts.description, ts.amount, ts.next_due,
                   ts.frequency, ts.anchor_day, ts.second_day,
                   af.account_name AS from_account_name, at.account_name AS to_account_name
            FROM transfer_schedules ts
            JOIN account af ON ts.from_account_id = af.account_id
            JOIN account at ON ts.to_account_id = at.account_id
            WHERE ts.is_active = true AND ts.user_id = %s AND ts.next_due <= %s
        """, (user_id, window_end))
        for desc, amount, next_due, freq, anchor, second, from_name, to_name in cursor.fetchall():
            label = (desc or '').strip() or f'Transfer {from_name} → {to_name}'
            for due in upcoming_occurrences(next_due, freq, anchor, second,
                                            window_start, window_end):
                items.append({'description': label, 'amount': float(amount),
                              'type': 'transfer', 'due': due.isoformat()})

    items.sort(key=lambda i: i['due'])
    return items


def compute_digest_facts(user_id, *, today=None):
    """Deterministic figure builder for the weekly digest — the ONLY source of
    numbers the email describes. Reuses compute_month_facts() for the month-to-date
    figures and adds the next 7 days of scheduled items. Reads the user's own rows
    only. Returns:

        {year, month, month_name,
         income, expenses, net, savings_rate,
         top_categories: [{name, amount}, ...],
         overruns: [{category, budget, actual, over}, ...],
         credit_cards: [{name, limit?, debt, available?, utilization_pct?,
                         apr?, est_monthly_interest?}, ...],
         upcoming: [{description, amount, type, due}, ...],
         upcoming_income, upcoming_expense}
    """
    today = today or date.today()
    month_facts = compute_month_facts(user_id, today.year, today.month)
    upcoming = _upcoming_scheduled(user_id, today)

    up_income = round(sum(i['amount'] for i in upcoming if i['type'] == 'income'), 2)
    up_expense = round(sum(i['amount'] for i in upcoming if i['type'] == 'expense'), 2)

    return {
        'year': today.year,
        'month': today.month,
        'month_name': _MONTHS[today.month],
        'income': month_facts['income'],
        'expenses': month_facts['expenses'],
        'net': month_facts['net'],
        'savings_rate': month_facts['savings_rate'],
        'top_categories': month_facts['top_categories'],
        'overruns': month_facts['overruns'],
        'credit_cards': month_facts['credit_cards'],
        'upcoming': upcoming,
        'upcoming_income': up_income,
        'upcoming_expense': up_expense,
    }


def _recipients(cursor, period_start):
    """Users due a digest: opted in, with an email, and not already sent for the
    current week (last_digest_sent_on before this week's Sunday). → [(id, email)]."""
    cursor.execute("""
        SELECT id, email FROM users
        WHERE weekly_digest = true AND email IS NOT NULL AND email <> ''
          AND (last_digest_sent_on IS NULL OR last_digest_sent_on < %s)
        ORDER BY id
    """, (period_start,))
    return cursor.fetchall()


def send_weekly_digests(*, today=None):
    """Send the weekly digest to every user due one, idempotently. Returns the
    number of digests actually sent. Each user is isolated in try/except so one
    failure (bad data, model error, send error) never aborts the batch. Callable
    with no request/app context (the scheduler thread) — establishes its own app
    context for template rendering and never touches current_user.
    """
    from app import app  # local import to avoid an import cycle at module load
    today = today or date.today()
    period_start = most_recent_sunday(today)
    reply_to = os.getenv('DIGEST_REPLY_TO') or None

    with db_cursor() as cursor:
        recipients = _recipients(cursor, period_start)

    sent = 0
    for user_id, email in recipients:
        try:
            facts = compute_digest_facts(user_id, today=today)
            narrative = generate_digest(facts)

            # Money agent (v10.10) — this week's findings, as their own email
            # section. Reuse the week's cached run if the user already triggered
            # one from the dashboard (don't re-pay Sonnet); otherwise run it now.
            # Its own try/except: one flaky investigation must not cost the user
            # their digest — the email just goes out without the section.
            agent_run = None
            try:
                with db_cursor() as cursor:
                    agent_run = load_agent_run(cursor, user_id,
                                               period_start=period_start)
                if agent_run is None:
                    agent_run = run_money_agent(user_id, today=today)
            except ParseError as e:
                app.logger.warning('Money agent skipped for user %s: %s',
                                   user_id, e)

            with app.app_context():
                html = render_template('emails/weekly_digest.html',
                                       narrative=narrative, facts=facts,
                                       agent_run=agent_run)
            mailer.send_email(email, DIGEST_SUBJECT, html, reply_to=reply_to)
            with db_cursor(commit=True) as cursor:
                cursor.execute(
                    "UPDATE users SET last_digest_sent_on = %s WHERE id = %s",
                    (today, user_id))
            sent += 1
        except (ParseError, mailer.MailError) as e:
            app.logger.warning('Weekly digest skipped for user %s: %s', user_id, e)
        except Exception as e:  # never let one user break the batch
            app.logger.exception('Weekly digest failed for user %s: %s', user_id, e)
    return sent


@click.command('send-digests')
@with_appcontext
def send_digests_command():
    """`flask send-digests` — run the weekly digest send now (manual/local use)."""
    n = send_weekly_digests()
    click.echo(f'Sent {n} weekly digest(s).')
