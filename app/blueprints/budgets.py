from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort,
    make_response
)
from flask_login import login_required, current_user
from app import limiter
from app.db import get_db_connection, db_cursor
from app.helpers import is_htmx, hx_toast, ai_enabled
from app.ai import propose_budgets, ParseError

bp = Blueprint('budgets', __name__)

# Cap the batch handed to the review model in one call (Auto-Categorize precedent).
BUDGET_REVIEW_CAP = 50


def build_budget_row(cursor, user_id, category_id):
    """Rebuild one cockpit row tuple after a set/clear, mirroring budgets():
    (category_id, name, effective, is_set, suggested, actual_this_month).
    Returns None if the category isn't this user's."""
    cursor.execute("SELECT id, name FROM categories WHERE id = %s AND user_id = %s",
                   (category_id, user_id))
    cat = cursor.fetchone()
    if cat is None:
        return None
    cid, name = cat
    cursor.execute("SELECT amount FROM budgets WHERE category_id = %s AND user_id = %s",
                   (cid, user_id))
    srow = cursor.fetchone()
    is_set = srow is not None
    saved_amt = float(srow[0]) if srow else None
    today = datetime.today()
    cursor.execute("""
        SELECT COALESCE(SUM(amount), 0) FROM transactions
        WHERE user_id = %s AND category_id = %s AND transaction_type = 'expense'
        AND is_adjustment = false AND is_transfer = false
        AND EXTRACT(YEAR FROM transaction_date) = %s
        AND EXTRACT(MONTH FROM transaction_date) = %s
    """, (user_id, cid, today.year, today.month))
    actual = float(cursor.fetchone()[0])
    suggested = compute_budget_suggestions(user_id).get(cid)
    effective = saved_amt if is_set else suggested
    return (cid, name, effective, is_set, suggested, actual)


def compute_budget_suggestions(user_id):
    """Suggested monthly budget per category = average monthly spend over the
    last 6 months. Non-adjustment, non-transfer expense transactions only.

    Returns a dict {category_id: suggested_amount (float)}. A category only
    appears once it has at least one month of qualifying history — brand-new
    users / categories get nothing, so the cockpit shows them as "—". The
    average is rounded to whole dollars — cents read oddly on a budget target.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            monthly.category_id,
            ROUND(AVG(monthly_total)::numeric, 0) AS suggested_budget
        FROM (
            SELECT
                category_id,
                DATE_TRUNC('month', transaction_date) AS month,
                SUM(amount) AS monthly_total
            FROM transactions
            WHERE user_id = %s AND transaction_type = 'expense'
            AND is_adjustment = false AND is_transfer = false
            AND transaction_date >= NOW() - INTERVAL '6 months'
            AND category_id IS NOT NULL
            GROUP BY category_id, DATE_TRUNC('month', transaction_date)
        ) monthly
        GROUP BY monthly.category_id
        HAVING COUNT(DISTINCT month) >= 1
    """, (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return {row[0]: float(row[1]) for row in rows}


def compute_budget_vs_actual(user_id, year=None, month=None):
    """Budget vs actual expense spending per category for one calendar month.

    Budgets are now a single monthly amount per category, so `budget` is that
    saved amount and `actual` is the sum of the user's non-adjustment,
    non-transfer expense transactions in that category during the given month.
    When `year`/`month` are omitted, the current calendar month is used (the
    monthly budget is constant, so an "all time" actual would be meaningless).
    Returns rows of (category, budget, actual, remaining).
    """
    if year and month:
        filter_year, filter_month = int(year), int(month)
    else:
        today = datetime.today()
        filter_year, filter_month = today.year, today.month
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            c.name AS category,
            b.amount AS budget,
            COALESCE(SUM(t.amount), 0) AS actual,
            b.amount - COALESCE(SUM(t.amount), 0) AS remaining
        FROM budgets b
        JOIN categories c ON b.category_id = c.id
        LEFT JOIN transactions t ON t.category_id = b.category_id
            AND t.transaction_type = 'expense'
            AND t.is_adjustment = false
            AND t.is_transfer = false
            AND EXTRACT(YEAR FROM t.transaction_date) = %s
            AND EXTRACT(MONTH FROM t.transaction_date) = %s
            AND t.user_id = b.user_id
        WHERE b.user_id = %s
        GROUP BY c.name, b.amount
        ORDER BY c.name
    """, (filter_year, filter_month, user_id))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def load_budget_rows(user_id):
    """Build the cockpit rows for every category — extracted from the budgets()
    view so the review-apply route can re-render the whole table. Returns
    (rows, all_categories); each row keeps the exact tuple shape
    (category_id, name, effective, is_set, suggested, actual_this_month) that
    partials/_budget_row.html indexes into."""
    today = datetime.today()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM categories WHERE user_id = %s ORDER BY name", (user_id,))
    all_categories = cursor.fetchall()
    cursor.execute("SELECT category_id, amount FROM budgets WHERE user_id = %s", (user_id,))
    saved = {row[0]: float(row[1]) for row in cursor.fetchall()}
    cursor.execute("""
        SELECT category_id, COALESCE(SUM(amount), 0)
        FROM transactions
        WHERE user_id = %s AND transaction_type = 'expense'
        AND is_adjustment = false AND is_transfer = false
        AND EXTRACT(YEAR FROM transaction_date) = %s
        AND EXTRACT(MONTH FROM transaction_date) = %s
        AND category_id IS NOT NULL
        GROUP BY category_id
    """, (user_id, today.year, today.month))
    actuals = {row[0]: float(row[1]) for row in cursor.fetchall()}
    cursor.close()
    conn.close()

    suggestions = compute_budget_suggestions(user_id)
    rows = []
    for cid, name in all_categories:
        is_set = cid in saved
        suggested = suggestions.get(cid)
        effective = saved[cid] if is_set else suggested
        rows.append((cid, name, effective, is_set, suggested, actuals.get(cid, 0.0)))
    return rows, all_categories


def compute_budget_review_facts(user_id, cap=BUDGET_REVIEW_CAP):
    """The deterministic per-category facts the review model narrates from —
    the app computes every figure; the model only proposes. Same rolling
    6-month window and filters as compute_budget_suggestions, so avg_monthly
    agrees with the cockpit's "Suggested" column (the current partial month is
    included; the system prompt flags it as in progress).

    Candidates are categories with at least one month of spend history OR a
    saved budget (a saved budget with no recent spend is exactly a "consider
    lowering this" candidate); categories with neither are skipped. Ordered by
    6-month total spend descending, capped at `cap`.
    """
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT t.category_id, c.name,
                   TO_CHAR(DATE_TRUNC('month', t.transaction_date), 'YYYY-MM') AS month,
                   SUM(t.amount) AS monthly_total
            FROM transactions t
            JOIN categories c ON t.category_id = c.id
            WHERE t.user_id = %s AND t.transaction_type = 'expense'
            AND t.is_adjustment = false AND t.is_transfer = false
            AND t.transaction_date >= NOW() - INTERVAL '6 months'
            AND t.category_id IS NOT NULL
            GROUP BY t.category_id, c.name, DATE_TRUNC('month', t.transaction_date)
            ORDER BY t.category_id, month
        """, (user_id,))
        spend_rows = cursor.fetchall()
        cursor.execute("""
            SELECT b.category_id, c.name, b.amount
            FROM budgets b
            JOIN categories c ON b.category_id = c.id
            WHERE b.user_id = %s
        """, (user_id,))
        budget_rows = cursor.fetchall()

    monthly = {}   # category_id -> {"name": ..., "totals": [(month, spent), ...]}
    for cid, name, month, total in spend_rows:
        entry = monthly.setdefault(cid, {"name": name, "totals": []})
        entry["totals"].append((month, float(total)))
    saved = {cid: (name, int(amount)) for cid, name, amount in budget_rows}

    current_month = datetime.today().strftime('%Y-%m')
    candidates = []
    for cid in set(monthly) | set(saved):
        name = monthly[cid]["name"] if cid in monthly else saved[cid][0]
        totals = monthly.get(cid, {}).get("totals", [])
        current_budget = saved[cid][1] if cid in saved else None
        spent_by_month = [{"month": m, "spent": round(s, 2)} for m, s in totals]
        avg = round(sum(s for _, s in totals) / len(totals), 2) if totals else None
        overruns = (sum(1 for _, s in totals if s > current_budget)
                    if current_budget is not None else 0)
        this_month = next((s for m, s in totals if m == current_month), 0.0)
        candidates.append({
            "name": name,
            "monthly_totals": spent_by_month,
            "months_with_spend": len(spent_by_month),
            "avg_monthly": avg,
            "current_budget": current_budget,
            "this_month_spent": round(this_month, 2),
            "overrun_months": overruns,
        })

    candidates.sort(key=lambda c: sum(m["spent"] for m in c["monthly_totals"]),
                    reverse=True)
    return {
        "current_month": current_month,
        "months_window": 6,
        "categories": candidates[:cap],
    }


@bp.route('/budgets')
@login_required
def budgets():
    """The budgets cockpit: one row per category showing the monthly target
    (saved override, else suggested average) alongside this month's actual."""
    rows, all_categories = load_budget_rows(current_user.id)
    return render_template('budgets.html', budget_rows=rows,
                           has_categories=bool(all_categories),
                           review_enabled=ai_enabled())


@bp.route('/budgets/set', methods=['POST'])
@login_required
def set_budget():
    """Upsert one category's monthly budget amount (the override that sticks)."""
    category_id = request.form.get('category_id')
    amount_str = request.form.get('amount', '').strip()
    errors = []
    if not category_id:
        errors.append('Category is required')
    if not amount_str:
        errors.append('Amount is required')
    else:
        try:
            amount = float(amount_str)
            if amount <= 0:
                errors.append('Amount must be greater than zero')
        except ValueError:
            errors.append('Amount must be a valid number')
    if errors:
        if is_htmx():
            return hx_toast(make_response('', 200), '; '.join(errors), 'error')
        for e in errors:
            flash(e)
        return redirect(url_for('budgets.budgets'))
    conn = get_db_connection()
    cursor = conn.cursor()
    # Guard ownership of the category before writing a budget against it.
    cursor.execute("SELECT 1 FROM categories WHERE id = %s AND user_id = %s", (category_id, current_user.id))
    if cursor.fetchone() is None:
        cursor.close(); conn.close()
        abort(404)
    try:
        # Budgets are whole-dollar targets — cents read oddly, so round on save.
        cursor.execute("""
            INSERT INTO budgets (category_id, amount, user_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, category_id) DO UPDATE SET amount = EXCLUDED.amount
        """, (category_id, round(float(amount_str)), current_user.id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        cursor.close(); conn.close()
        if is_htmx():
            return hx_toast(make_response('', 200), f'Error: {e}', 'error')
        flash(f'Error: {e}')
        return redirect(url_for('budgets.budgets'))
    if is_htmx():
        row = build_budget_row(cursor, current_user.id, category_id)
        cursor.close(); conn.close()
        resp = make_response(render_template('partials/_budget_row.html', row=row))
        return hx_toast(resp, 'Budget saved')
    cursor.close()
    conn.close()
    flash('Budget saved')
    return redirect(url_for('budgets.budgets'))


@bp.route('/budgets/clear', methods=['POST'])
@login_required
def clear_budget():
    """Delete a category's override so it reverts to the suggested average."""
    category_id = request.form.get('category_id')
    if not category_id:
        abort(404)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM budgets WHERE category_id = %s AND user_id = %s", (category_id, current_user.id))
    if cursor.fetchone() is None:
        cursor.close(); conn.close()
        abort(404)
    try:
        cursor.execute("DELETE FROM budgets WHERE category_id = %s AND user_id = %s", (category_id, current_user.id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        cursor.close(); conn.close()
        if is_htmx():
            return hx_toast(make_response('', 200), f'Error: {e}', 'error')
        flash(f'Error: {e}')
        return redirect(url_for('budgets.budgets'))
    if is_htmx():
        row = build_budget_row(cursor, current_user.id, category_id)
        cursor.close(); conn.close()
        resp = make_response(render_template('partials/_budget_row.html', row=row))
        return hx_toast(resp, 'Budget cleared — reverted to suggested')
    cursor.close()
    conn.close()
    flash('Budget cleared — reverted to suggested')
    return redirect(url_for('budgets.budgets'))


@bp.route('/budgets/review/scan', methods=['POST'])
@limiter.limit("10 per minute")
@login_required
def budget_review_scan():
    """Run one batched review over the user's budget candidates and render the
    proposal list. Proposals matching the already-saved amount are filtered as
    no-ops. Any failure / nothing to propose falls back to an error toast
    without clearing the panel. Nothing is applied here — the user confirms."""
    if not ai_enabled():
        abort(404)

    def _toast_only(message):
        resp = hx_toast(make_response('', 200), message, 'error')
        resp.headers['HX-Reswap'] = 'none'
        return resp

    facts = compute_budget_review_facts(current_user.id)
    if not facts['categories']:
        return _toast_only('Not enough history to review — add some transactions first')

    with db_cursor() as cursor:
        cursor.execute("SELECT id, name FROM categories WHERE user_id = %s ORDER BY name",
                       (current_user.id,))
        all_categories = cursor.fetchall()
        cursor.execute("SELECT category_id, amount FROM budgets WHERE user_id = %s",
                       (current_user.id,))
        saved = {row[0]: int(row[1]) for row in cursor.fetchall()}

    try:
        review = propose_budgets(facts, all_categories)
    except ParseError:
        return _toast_only("Couldn't run the review right now — try again")

    suggestions = compute_budget_suggestions(current_user.id)
    items = []
    for p in review['proposals']:
        cid = p['category_id']
        current = saved.get(cid)
        if current is not None and p['amount'] == current:
            continue  # no-op — nothing to confirm
        items.append({
            'id': cid,
            'name': p['category_name'],
            'current': current,
            'suggested': suggestions.get(cid),
            'proposed': p['amount'],
            'reason': p['reason'],
        })

    if not items:
        return _toast_only('Your budgets already look right-sized — nothing to change')

    resp = make_response(render_template(
        'partials/_budget_review.html', summary=review['summary'], items=items))
    return hx_toast(resp, f'{len(items)} proposal{"s" if len(items) != 1 else ""} to review')


@bp.route('/budgets/review/apply', methods=['POST'])
@limiter.limit("10 per minute")
@login_required
def budget_review_apply():
    """Apply the checked proposals — for each checked category, upsert its
    monthly budget to the (possibly user-edited) amount. Guards category
    ownership BEFORE each write, so the form can't reach another user's rows;
    one bad row skips, it doesn't kill the batch. Returns the refreshed whole
    table (every effective amount may shift) + an OOB swap that clears the
    review panel. Nothing auto-applies — only rows the user checked."""
    if not ai_enabled():
        abort(404)

    checked = request.form.getlist('category_id')
    applied = 0
    with db_cursor(commit=True) as cursor:
        for cid in checked:
            try:
                category_id = int(cid)
            except (TypeError, ValueError):
                continue
            cursor.execute("SELECT 1 FROM categories WHERE id = %s AND user_id = %s",
                           (category_id, current_user.id))
            if cursor.fetchone() is None:
                continue  # not the user's category — skip (write-side IDOR guard)
            try:
                amount = int(round(float(request.form.get(f'amount_{category_id}', ''))))
            except (TypeError, ValueError):
                continue
            if amount <= 0:
                continue
            cursor.execute("""
                INSERT INTO budgets (category_id, amount, user_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id, category_id) DO UPDATE SET amount = EXCLUDED.amount
            """, (category_id, amount, current_user.id))
            applied += 1

    rows, _ = load_budget_rows(current_user.id)
    resp = make_response(
        render_template('partials/_budget_rows.html', budget_rows=rows)
        + '<div id="budget-review-panel" hx-swap-oob="true"></div>')
    message = (f'{applied} budget{"s" if applied != 1 else ""} updated'
               if applied else 'Nothing applied')
    return hx_toast(resp, message, 'success' if applied else 'error')
