"""Small shared, DB-free helpers used across blueprints."""
import json
import math
import os
from datetime import datetime, timedelta

from flask import request

# The one user-facing message for an unexpected write failure. Raw exception
# text (psycopg2 constraint names, SQL fragments) stays in the server log —
# pair every use with current_app.logger.exception().
GENERIC_ERROR = 'Something went wrong — please try again'


def ai_enabled():
    """True when the NL quick-add (v9) is configured — i.e. an ANTHROPIC_API_KEY
    is present. Templates gate the quick-add box on this so the feature stays
    invisible until the key is set."""
    return bool(os.getenv('ANTHROPIC_API_KEY'))


def parse_signed_amount(raw, label='Amount'):
    """Validate a signed money form field (a bank balance can be negative —
    credit cards — or exactly zero). Returns (amount, error) — exactly one is
    None.

    float() happily parses 'nan' and 'inf', and neither trips a naive range
    check (NaN compares False to everything) — but Postgres stores NaN in a
    numeric column, and one NaN row poisons every SUM() the dashboards
    aggregate. So reject non-finite values along with non-numbers.
    """
    raw = (raw or '').strip()
    if not raw:
        return None, f'{label} is required'
    try:
        amount = float(raw)
    except ValueError:
        return None, f'{label} must be a valid number'
    if not math.isfinite(amount):
        return None, f'{label} must be a valid number'
    return amount, None


def parse_positive_amount(raw, label='Amount'):
    """Validate a money form field. Returns (amount, error) — exactly one is
    None. Shared by every amount-taking form (transactions, schedules,
    transfers, budgets, goals) so they can't drift. The finite/NaN guard lives
    in parse_signed_amount; this adds the strictly-positive check.
    """
    amount, error = parse_signed_amount(raw, label)
    if error:
        return None, error
    if amount <= 0:
        return None, f'{label} must be greater than zero'
    return amount, None


def parse_month_param(raw):
    """Validate a 'YYYY-MM' month filter off a query string. Returns the string
    when it parses, else None (= no month filter) — a tampered ?month must fall
    back to the unfiltered page, never 500 in strptime/EXTRACT."""
    raw = (raw or '').strip()
    try:
        datetime.strptime(raw, '%Y-%m')
    except ValueError:
        return None
    return raw


def parse_page_param(raw):
    """Pagination number off a query string: a positive int, else 1. Clamped —
    page 0/negative would flow into a negative SQL OFFSET (a DB error), not
    just a weird page."""
    try:
        page = int(raw)
    except (TypeError, ValueError):
        return 1
    return max(page, 1)


def parse_int_param(raw):
    """A posted id field: int, or None when missing/blank/non-numeric. Callers
    treat None as 'not provided' — their existing required/ownership checks do
    the erroring; this just keeps garbage out of psycopg2 (a non-numeric string
    against an integer column raises, i.e. a 500)."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def is_htmx():
    """True when the request came from an HTMX swap (vs. a full navigation)."""
    return request.headers.get('HX-Request') == 'true'


def hx_toast(response, message, kind='success'):
    """Attach an HX-Trigger header so base.html fires a toast after the swap."""
    response.headers['HX-Trigger'] = json.dumps(
        {'showToast': {'message': message, 'kind': kind}}
    )
    return response


def most_recent_sunday(today):
    """The date of the most recent Sunday on or before `today` — the weekly
    period boundary shared by the digest's idempotency guard and the money
    agent's run key. (weekday(): Mon=0 … Sun=6.) Lives here so digests.py and
    agent.py can both use it without importing each other."""
    return today - timedelta(days=(today.weekday() + 1) % 7)


def recent_months(count=12, today=None):
    """Return the last `count` months as 'YYYY-MM' labels, newest first.

    Used by the History and Dashboard month filters so the two stay in sync.
    """
    today = today or datetime.today()
    months = []
    for i in range(count):
        month = today.month - i
        year = today.year
        if month <= 0:
            month += 12
            year -= 1
        months.append(f'{year}-{month:02d}')
    return months
