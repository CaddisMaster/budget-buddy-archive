"""Legacy /analytics URL — the page merged into the dashboard (v10.9).

The redirect keeps old bookmarks alive and carries the month filter across.
The content analytics uniquely owned (Ask box, year over year, spending by
day of week) lives on /dashboard now; the tabular echoes of the dashboard
charts were dropped. No @login_required — /dashboard enforces auth after
the hop.
"""
from flask import Blueprint, redirect, request, url_for

bp = Blueprint('analytics', __name__)


@bp.route('/analytics')
def analytics():
    return redirect(url_for('main.dashboard', month=request.args.get('month')))
