"""Small shared, DB-free helpers used across blueprints."""
import json
import os
from datetime import datetime

from flask import request


def ai_enabled():
    """True when the NL quick-add (v9) is configured — i.e. an ANTHROPIC_API_KEY
    is present. Templates gate the quick-add box on this so the feature stays
    invisible until the key is set."""
    return bool(os.getenv('ANTHROPIC_API_KEY'))


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
