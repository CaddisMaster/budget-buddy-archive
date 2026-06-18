from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from app.db import get_db_connection

bp = Blueprint('budgets', __name__)


def compute_budget_vs_actual(user_id, year=None, month=None):
    """Budget vs actual expense spending per category.

    For each budget, `actual` is the sum of that user's non-adjustment expense
    transactions in the same category whose date falls inside the budget's
    period; `remaining` is budget minus actual (negative = over budget). When
    `year` and `month` are given, only budgets whose period_start is in that
    month are included. Returns rows of (category, budget, actual, remaining).
    """
    params = [user_id]
    month_filter = ""
    if year and month:
        month_filter = (
            "AND EXTRACT(YEAR FROM b.period_start) = %s "
            "AND EXTRACT(MONTH FROM b.period_start) = %s"
        )
        params.extend([year, month])
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f"""
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
            AND t.transaction_date BETWEEN b.period_start AND b.period_end
            AND t.user_id = b.user_id
        WHERE b.user_id = %s
        {month_filter}
        GROUP BY c.name, b.amount, b.period_start, b.period_end
        ORDER BY c.name
    """, params)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


@bp.route('/budgets', methods=['GET', 'POST'])
@login_required
def budgets():
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        category_id = request.form.get('category_id')
        amount_str = request.form.get('amount', '').strip()
        period_start = request.form.get('period_start', '').strip()
        period_end = request.form.get('period_end', '').strip()
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
        if not period_start:
            errors.append('Period start is required')
        if not period_end:
            errors.append('Period end is required')
        if period_start and period_end and period_end <= period_start:
            errors.append('Period end must be after period start')
        if errors:
            cursor.close(); conn.close()
            for e in errors:
                flash(e)
            return redirect(url_for('budgets.budgets'))
        amount = float(amount_str)
        try:
            cursor.execute(
                "INSERT INTO budgets (category_id, amount, period_start, period_end, user_id) VALUES (%s, %s, %s, %s, %s)",
                (category_id, amount, period_start, period_end, current_user.id)
            )
            conn.commit()
            flash('Budget added successfully')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect(url_for('budgets.budgets'))
    cursor.execute("SELECT id, name FROM categories WHERE user_id = %s ORDER BY name", (current_user.id,))
    all_categories = cursor.fetchall()
    cursor.execute("""
        SELECT b.id, c.name, b.amount, b.period_start, b.period_end
        FROM budgets b
        JOIN categories c ON b.category_id = c.id
        WHERE b.user_id = %s
        ORDER BY b.period_start DESC
    """, (current_user.id,))
    all_budgets = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('budgets.html', categories=all_categories, budgets=all_budgets)


@bp.route('/budgets/edit/<int:budget_id>', methods=['GET', 'POST'])
@login_required
def edit_budget(budget_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM budgets WHERE id = %s AND user_id = %s", (budget_id, current_user.id))
    if cursor.fetchone() is None:
        cursor.close(); conn.close()
        abort(404)
    if request.method == 'POST':
        category_id = request.form.get('category_id')
        amount_str = request.form.get('amount', '').strip()
        period_start = request.form.get('period_start', '').strip()
        period_end = request.form.get('period_end', '').strip()
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
        if not period_start:
            errors.append('Period start is required')
        if not period_end:
            errors.append('Period end is required')
        if period_start and period_end and period_end <= period_start:
            errors.append('Period end must be after period start')
        if errors:
            cursor.close(); conn.close()
            for e in errors:
                flash(e)
            return redirect(url_for('budgets.edit_budget', budget_id=budget_id))
        amount = float(amount_str)
        try:
            cursor.execute(
                "UPDATE budgets SET category_id=%s, amount=%s, period_start=%s, period_end=%s WHERE id=%s AND user_id=%s",
                (category_id, amount, period_start, period_end, budget_id, current_user.id)
            )
            conn.commit()
            flash('Budget updated successfully')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect(url_for('budgets.budgets'))
    cursor.execute("SELECT id, category_id, amount, period_start, period_end FROM budgets WHERE id = %s AND user_id = %s", (budget_id, current_user.id))
    budget = cursor.fetchone()
    cursor.execute("SELECT id, name FROM categories WHERE user_id = %s ORDER BY name", (current_user.id,))
    all_categories = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('edit_budget.html', budget=budget, categories=all_categories)


@bp.route('/budgets/delete/<int:budget_id>', methods=['GET', 'POST'])
@login_required
def delete_budget(budget_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM budgets WHERE id = %s AND user_id = %s", (budget_id, current_user.id))
    if cursor.fetchone() is None:
        cursor.close(); conn.close()
        abort(404)
    if request.method == 'POST':
        try:
            cursor.execute("DELETE FROM budgets WHERE id = %s AND user_id = %s", (budget_id, current_user.id))
            conn.commit()
            flash('Budget deleted')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect(url_for('budgets.budgets'))
    cursor.execute("""
        SELECT b.id, c.name, b.amount, b.period_start, b.period_end
        FROM budgets b JOIN categories c ON b.category_id = c.id
        WHERE b.id = %s AND b.user_id = %s
    """, (budget_id, current_user.id))
    budget = cursor.fetchone()
    cursor.close()
    conn.close()
    return render_template('delete_budget.html', budget=budget)
