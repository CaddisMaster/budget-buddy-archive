from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort,
    make_response
)
from flask_login import login_required, current_user
from app.db import get_db_connection
from app.helpers import is_htmx, hx_toast

bp = Blueprint('budgets', __name__)


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


@bp.route('/budgets')
@login_required
def budgets():
    """The budgets cockpit: one row per category showing the monthly target
    (saved override, else suggested average) alongside this month's actual."""
    today = datetime.today()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM categories WHERE user_id = %s ORDER BY name", (current_user.id,))
    all_categories = cursor.fetchall()
    cursor.execute("SELECT category_id, amount FROM budgets WHERE user_id = %s", (current_user.id,))
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
    """, (current_user.id, today.year, today.month))
    actuals = {row[0]: float(row[1]) for row in cursor.fetchall()}
    cursor.close()
    conn.close()

    suggestions = compute_budget_suggestions(current_user.id)
    rows = []
    for cid, name in all_categories:
        is_set = cid in saved
        suggested = suggestions.get(cid)
        effective = saved[cid] if is_set else suggested
        rows.append((cid, name, effective, is_set, suggested, actuals.get(cid, 0.0)))
    return render_template('budgets.html', budget_rows=rows, has_categories=bool(all_categories))


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
