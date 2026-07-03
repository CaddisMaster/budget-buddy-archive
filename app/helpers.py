"""Small shared, DB-free helpers used across blueprints."""
import json
import math
import os
from datetime import datetime

from flask import request


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


def is_htmx():
    """True when the request came from an HTMX swap (vs. a full navigation)."""
    return request.headers.get('HX-Request') == 'true'


def hx_toast(response, message, kind='success'):
    """Attach an HX-Trigger header so base.html fires a toast after the swap."""
    response.headers['HX-Trigger'] = json.dumps(
        {'showToast': {'message': message, 'kind': kind}}
    )
    return response


def recent_months(count=12, today=None):
    """Return the last `count` months as 'YYYY-MM' labels, newest first.

    Used by the History and Analytics month filters so the two stay in sync.
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
