import json
import math
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort,
    make_response, current_app
)
from flask_login import login_required, current_user
from app import limiter
from app.ai import generate_goal_coach, ParseError, MODEL
from app.db import get_db_connection
from app.helpers import (
    is_htmx, hx_toast, ai_enabled, parse_positive_amount, GENERIC_ERROR
)

bp = Blueprint('goals', __name__)

GOAL_SELECT = (
    "SELECT g.id, g.name, g.target_amount, g.target_date, g.account_id, "
    "g.baseline_amount, g.goal_type, a.account_name "
    "FROM goals g JOIN account a ON g.account_id = a.account_id "
    "WHERE g.user_id = %s"
)

# 'save' works toward a positive target; 'payoff' (v10.9) works a negative
# balance back to $0. A payoff goal snapshots at creation: baseline_amount =
# the account's balance and target_amount = the starting debt (−balance), so
# compute_goal_projection() is shared — the type only drives wording/forms.
GOAL_TYPES = ('save', 'payoff')

# How many recent months feed the "projected completion" pace estimate.
INFLOW_WINDOW_MONTHS = 3


def compute_goal_projection(target_amount, saved, target_date,
                            monthly_net_inflow, today=None):
    """Pure projection math for a savings goal — no DB access, easy to unit test
    (sibling of compute_next_due()).

    Inputs are plain numbers/dates; the caller feeds in `saved` (account balance
    minus the goal's baseline) and `monthly_net_inflow` (recent average net flow
    into the linked account). Returns a dict describing progress and two
    forward-looking projections:

      - required_per_month: with a target_date, how much to set aside each month
        to hit the target on time (None if no date or already complete).
      - projected_date / on_track: from the recent inflow pace, when the goal is
        projected to complete and whether that beats the target_date.
    """
    today = today or date.today()
    target_amount = float(target_amount)
    saved = float(saved)
    monthly_net_inflow = float(monthly_net_inflow)

    remaining = round(target_amount - saved, 2)
    percent = round(saved / target_amount * 100, 1) if target_amount > 0 else 0.0
    percent = max(0.0, min(percent, 100.0))
    complete = remaining <= 0

    required_per_month = None
    if target_date and not complete:
        delta = relativedelta(target_date, today)
        months_left = delta.years * 12 + delta.months + (1 if delta.days > 0 else 0)
        if months_left <= 0:
            months_left = 1  # target is here/overdue — needs it within the month
        required_per_month = round(remaining / months_left, 2)

    projected_date = None
    on_track = None
    if not complete:
        if monthly_net_inflow > 0:
            months_to_finish = math.ceil(remaining / monthly_net_inflow)
            projected_date = today + relativedelta(months=months_to_finish)
            if target_date:
                on_track = projected_date <= target_date
        else:
            # No positive inflow → never projected to finish at the current pace.
            if target_date:
                on_track = False
    elif target_date:
        on_track = True

    return {
        'remaining': remaining,
        'percent': percent,
        'complete': complete,
        'required_per_month': required_per_month,
        'projected_date': projected_date,
        'on_track': on_track,
        'monthly_net_inflow': round(monthly_net_inflow, 2),
    }


def _account_balance(cursor, user_id, account_id):
    """Net income − expense for one account (transfers included — they move real
    money). Same shape as the per-account balance on the Accounts page."""
    cursor.execute(
        "SELECT COALESCE(SUM(CASE WHEN transaction_type = 'income' "
        "THEN amount ELSE -amount END), 0) "
        "FROM transactions WHERE user_id = %s AND account_id = %s",
        (user_id, account_id),
    )
    return float(cursor.fetchone()[0])


def _recent_monthly_inflow(cursor, user_id, account_id, months=INFLOW_WINDOW_MONTHS):
    """Average net inflow per month into an account over the last `months`
    months — drives the projected-completion estimate. Transfers count: a
    transfer into the account is exactly the kind of contribution we want."""
    cursor.execute(
        "SELECT COALESCE(SUM(CASE WHEN transaction_type = 'income' "
        "THEN amount ELSE -amount END), 0) "
        "FROM transactions WHERE user_id = %s AND account_id = %s "
        "AND transaction_date >= %s",
        (user_id, account_id, date.today() - relativedelta(months=months)),
    )
    total = float(cursor.fetchone()[0])
    return total / months


def _fetch_accounts(cursor, user_id):
    cursor.execute(
        "SELECT account_id, account_name FROM account WHERE user_id = %s ORDER BY account_name",
        (user_id,),
    )
    return cursor.fetchall()


def _validate(form, user_account_ids):
    errors = []
    name = form.get('name', '').strip()
    target_date_str = form.get('target_date', '').strip()
    account_id = form.get('account_id') or None
    goal_type = form.get('goal_type', 'save')
    if goal_type not in GOAL_TYPES:
        errors.append('Invalid goal type')
        goal_type = 'save'

    if not name:
        errors.append('Name is required')
    elif len(name) > 80:
        errors.append('Name must be 80 characters or fewer')
    target_amount = None
    if goal_type == 'save':
        # A payoff goal's target is derived (the starting debt), never typed in.
        target_amount, amount_error = parse_positive_amount(
            form.get('target_amount'), label='Target amount')
        if amount_error:
            errors.append(amount_error)
    try:
        account_ok = account_id and int(account_id) in user_account_ids
    except (TypeError, ValueError):
        account_ok = False
    if not account_ok:
        errors.append('A valid linked account is required')
    target_date = None
    if target_date_str:
        try:
            target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
        except ValueError:
            errors.append('Target date must be a valid date')

    fields = {
        'name': name,
        'target_amount': target_amount,
        'target_date': target_date,
        'account_id': account_id,
        'goal_type': goal_type,
    }
    return fields, errors


def _goal_view(cursor, user_id, row):
    """Turn one goal row (GOAL_SELECT shape) into a view dict with progress +
    projections. For a payoff goal the same math reads as: saved = paid off
    since creation, remaining = the current actual debt (self-correcting if
    more gets charged), complete = balance back at $0."""
    (gid, name, target_amount, target_date, account_id,
     baseline, goal_type, account_name) = row
    balance = _account_balance(cursor, user_id, account_id)
    saved = balance - float(baseline)
    inflow = _recent_monthly_inflow(cursor, user_id, account_id)
    projection = compute_goal_projection(target_amount, saved, target_date, inflow)
    return {
        'id': gid,
        'name': name,
        'type': goal_type,
        'target_amount': float(target_amount),
        'target_date': target_date,
        'account_name': account_name,
        'saved': round(saved, 2),
        **projection,
    }


def build_goals_view(cursor, user_id):
    """Load a user's goals with computed progress + projections. Shared by the
    Goals page and the dashboard widget so the math lives in one place."""
    cursor.execute(GOAL_SELECT + " ORDER BY g.created_at DESC", (user_id,))
    return [_goal_view(cursor, user_id, r) for r in cursor.fetchall()]


def build_single_goal_view(cursor, user_id, goal_id):
    """View dict for one goal, or None if it isn't this user's."""
    cursor.execute(GOAL_SELECT + " AND g.id = %s", (user_id, goal_id))
    row = cursor.fetchone()
    return _goal_view(cursor, user_id, row) if row else None


# --- v10.8 Goal Coach — deterministic facts + cache (twin of Insight) --------
#
# The app computes every figure (build_goals_view + compute_goal_projection);
# app.ai.generate_goal_coach() only narrates them. Nothing the model returns is
# ever used as a number.

def compute_goal_coach_facts(cursor, user_id):
    """The ONLY source of figures the coach narrates — reshapes each goal's
    already-computed view into a JSON-friendly per-goal fact plus rollups. Reads
    the user's own rows only. Returns:

        {goals: [{name, target_amount, saved, percent, remaining, complete,
                  on_track, required_per_month, projected_date, target_date,
                  monthly_net_inflow}, ...],
         count, incomplete_count, on_track_count, total_saved, total_target}
    """
    goals = build_goals_view(cursor, user_id)
    goal_facts = [{
        'name': g['name'],
        'type': g['type'],
        'target_amount': g['target_amount'],
        'saved': g['saved'],
        'percent': g['percent'],
        'remaining': g['remaining'],
        'complete': g['complete'],
        'on_track': g['on_track'],
        'required_per_month': g['required_per_month'],
        'projected_date': g['projected_date'],
        'target_date': g['target_date'],
        'monthly_net_inflow': g['monthly_net_inflow'],
    } for g in goals]
    return {
        'goals': goal_facts,
        'count': len(goals),
        'incomplete_count': sum(1 for g in goals if not g['complete']),
        'on_track_count': sum(1 for g in goals if g['on_track'] is True),
        'total_saved': round(sum(g['saved'] for g in goals), 2),
        'total_target': round(sum(g['target_amount'] for g in goals), 2),
    }


def load_goal_coach(cursor, user_id, year, month):
    """Return the cached coaching for (user, month) as {summary, tips,
    created_at}, or None. Takes an existing cursor so /goals reuses its
    connection. No model call on read."""
    cursor.execute("""
        SELECT content, created_at FROM goal_coach
        WHERE user_id = %s AND year = %s AND month = %s
    """, (user_id, int(year), int(month)))
    row = cursor.fetchone()
    if row is None:
        return None
    data = json.loads(row[0])
    data['created_at'] = row[1]
    return data


@bp.route('/goals', methods=['GET', 'POST'])
@login_required
def goals():
    conn = get_db_connection()
    cursor = conn.cursor()
    account_ids = {a[0] for a in _fetch_accounts(cursor, current_user.id)}

    if request.method == 'POST':
        fields, errors = _validate(request.form, account_ids)
        if errors:
            cursor.close(); conn.close()
            if is_htmx():
                return hx_toast(make_response('', 200), '; '.join(errors), 'error')
            for e in errors:
                flash(e)
            return redirect(url_for('goals.goals'))
        if fields['goal_type'] == 'payoff':
            # Snapshot the debt: baseline = the (negative) balance now, target =
            # what it takes to get back to $0. The projection math then reads
            # saved as "paid off" and remaining as "still owed".
            balance = _account_balance(cursor, current_user.id, fields['account_id'])
            if balance >= 0:
                cursor.close(); conn.close()
                msg = "That account's balance is already $0 or positive — nothing to pay off"
                if is_htmx():
                    return hx_toast(make_response('', 200), msg, 'error')
                flash(msg)
                return redirect(url_for('goals.goals'))
            baseline = balance
            fields['target_amount'] = round(-balance, 2)
        # Baseline decides whether the account's existing balance counts toward
        # a SAVE goal: "existing" → 0; "fresh" → current balance (so back-to-back
        # goals on one account start from zero progress).
        elif request.form.get('baseline_mode') == 'fresh':
            baseline = _account_balance(cursor, current_user.id, fields['account_id'])
        else:
            baseline = 0
        try:
            cursor.execute(
                "INSERT INTO goals (name, target_amount, target_date, account_id, "
                "baseline_amount, goal_type, user_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (fields['name'], fields['target_amount'], fields['target_date'],
                 fields['account_id'], baseline, fields['goal_type'], current_user.id),
            )
            new_id = cursor.fetchone()[0]
            conn.commit()
        except Exception:
            conn.rollback()
            cursor.close(); conn.close()
            current_app.logger.exception('create goal failed')
            if is_htmx():
                return hx_toast(make_response('', 200), GENERIC_ERROR, 'error')
            flash(GENERIC_ERROR)
            return redirect(url_for('goals.goals'))
        if is_htmx():
            g = build_single_goal_view(cursor, current_user.id, new_id)
            cursor.close(); conn.close()
            resp = make_response(render_template('partials/_goal_card.html', g=g))
            return hx_toast(resp, 'Goal added')
        cursor.close(); conn.close()
        flash('Goal added successfully')
        return redirect(url_for('goals.goals'))

    goals_view = build_goals_view(cursor, current_user.id)
    all_accounts = _fetch_accounts(cursor, current_user.id)
    # Goal Coach card (gated on the AI key + at least one in-progress goal). Read
    # the cache only — never a model call on page load, mirroring the dashboard
    # Insight/Forecast cards.
    today = date.today()
    ai_on = ai_enabled()
    coach = coach_facts = None
    show_coach = False
    if ai_on:
        coach_facts = compute_goal_coach_facts(cursor, current_user.id)
        show_coach = coach_facts['incomplete_count'] > 0
        if show_coach:
            coach = load_goal_coach(cursor, current_user.id, today.year, today.month)
    cursor.close()
    conn.close()
    return render_template('goals.html', goals=goals_view, accounts=all_accounts,
                           ai_enabled=ai_on, show_coach=show_coach,
                           coach=coach, coach_facts=coach_facts)


@bp.route('/goals/coach/generate', methods=['POST'])
@limiter.limit("10 per minute")
@login_required
def coach_generate():
    """Generate (or Regenerate) this month's goal coaching: compute the facts,
    have Claude narrate them once, cache the result, and swap the card.

    HTMX endpoint — the /goals coach card posts here and swaps itself
    (#goal-coach-card) with the returned fragment. Any failure falls back to the
    un-generated card + an error toast, so the page never breaks."""
    today = datetime.today()
    year, month = today.year, today.month
    conn = get_db_connection()
    cursor = conn.cursor()
    facts = compute_goal_coach_facts(cursor, current_user.id)

    def _card(coach):
        return make_response(render_template(
            'partials/_goal_coach_card.html',
            coach=coach, facts=facts, ai_enabled=True))

    if facts['incomplete_count'] == 0:
        cursor.close(); conn.close()
        return hx_toast(_card(None), 'No goals in progress to coach yet', 'error')

    try:
        result = generate_goal_coach(facts)
    except ParseError:
        cursor.close(); conn.close()
        return hx_toast(_card(None), "Couldn't generate coaching right now", 'error')

    try:
        cursor.execute("""
            INSERT INTO goal_coach (user_id, year, month, content, model)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id, year, month)
            DO UPDATE SET content = EXCLUDED.content, model = EXCLUDED.model,
                          created_at = now()
        """, (current_user.id, year, month, json.dumps(result), MODEL))
        conn.commit()
    except Exception:
        conn.rollback()
        cursor.close(); conn.close()
        return hx_toast(_card(None), "Couldn't save coaching right now", 'error')
    cursor.close(); conn.close()

    coach = dict(result)
    coach['created_at'] = today
    return hx_toast(_card(coach), 'Coaching ready')


@bp.route('/goals/<int:goal_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_goal(goal_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT goal_type FROM goals WHERE id = %s AND user_id = %s",
                   (goal_id, current_user.id))
    row = cursor.fetchone()
    if row is None:
        cursor.close(); conn.close()
        abort(404)
    # The STORED type decides what an edit may touch — never the posted one.
    stored_type = row[0]
    all_accounts = _fetch_accounts(cursor, current_user.id)
    account_ids = {a[0] for a in all_accounts}

    if request.method == 'POST':
        fields, errors = _validate(request.form, account_ids)
        if errors:
            goal = (goal_id, fields['name'], request.form.get('target_amount', ''),
                    fields['target_date'], int(fields['account_id']) if fields['account_id'] else None,
                    stored_type)
            cursor.close(); conn.close()
            return render_template('partials/_goal_edit_card.html',
                                   goal=goal, accounts=all_accounts, errors=errors)
        try:
            if stored_type == 'payoff':
                # Account + target stay locked to the creation snapshot — moving
                # the goal to another account would need a re-baseline (delete +
                # recreate covers that). Only the name and deadline move.
                cursor.execute(
                    "UPDATE goals SET name=%s, target_date=%s "
                    "WHERE id=%s AND user_id=%s",
                    (fields['name'], fields['target_date'], goal_id, current_user.id),
                )
            else:
                cursor.execute(
                    "UPDATE goals SET name=%s, target_amount=%s, target_date=%s, "
                    "account_id=%s WHERE id=%s AND user_id=%s",
                    (fields['name'], fields['target_amount'], fields['target_date'],
                     fields['account_id'], goal_id, current_user.id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            current_app.logger.exception('edit goal failed')
            goal = (goal_id, fields['name'], request.form.get('target_amount', ''),
                    fields['target_date'], int(fields['account_id']) if fields['account_id'] else None,
                    stored_type)
            cursor.close(); conn.close()
            return render_template('partials/_goal_edit_card.html',
                                   goal=goal, accounts=all_accounts, errors=[GENERIC_ERROR])
        g = build_single_goal_view(cursor, current_user.id, goal_id)
        cursor.close(); conn.close()
        resp = make_response(render_template('partials/_goal_card.html', g=g))
        return hx_toast(resp, 'Goal updated')

    cursor.execute(
        "SELECT id, name, target_amount, target_date, account_id, goal_type "
        "FROM goals WHERE id = %s AND user_id = %s",
        (goal_id, current_user.id),
    )
    goal = cursor.fetchone()
    cursor.close()
    conn.close()
    return render_template('partials/_goal_edit_card.html', goal=goal, accounts=all_accounts)


@bp.route('/goals/<int:goal_id>/card')
@login_required
def goal_card(goal_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    g = build_single_goal_view(cursor, current_user.id, goal_id)
    cursor.close()
    conn.close()
    if g is None:
        abort(404)
    return render_template('partials/_goal_card.html', g=g)


@bp.route('/goals/<int:goal_id>', methods=['DELETE'])
@login_required
def delete_goal(goal_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM goals WHERE id = %s AND user_id = %s",
                   (goal_id, current_user.id))
    if cursor.fetchone() is None:
        cursor.close(); conn.close()
        abort(404)
    try:
        cursor.execute("DELETE FROM goals WHERE id = %s AND user_id = %s",
                       (goal_id, current_user.id))
        conn.commit()
    except Exception:
        conn.rollback()
        current_app.logger.exception('delete goal failed')
        g = build_single_goal_view(cursor, current_user.id, goal_id)
        cursor.close(); conn.close()
        resp = make_response(render_template('partials/_goal_card.html', g=g))
        return hx_toast(resp, GENERIC_ERROR, 'error')
    cursor.close()
    conn.close()
    return hx_toast(make_response('', 200), 'Goal deleted')
