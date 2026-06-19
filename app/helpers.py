"""Small shared, DB-free helpers used across blueprints."""
import json
from datetime import datetime

from flask import request


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
