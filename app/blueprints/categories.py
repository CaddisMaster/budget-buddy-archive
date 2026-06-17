from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from app.db import get_db_connection

bp = Blueprint('categories', __name__)


@bp.route('/categories', methods=['GET', 'POST'])
@login_required
def categories():
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        name = request.form['name'].strip()
        description = request.form.get('description', '').strip()
        if not name:
            cursor.close(); conn.close()
            flash('Name is required')
            return redirect(url_for('categories.categories'))
        if len(name) > 50:
            cursor.close(); conn.close()
            flash('Name must be 50 characters or fewer')
            return redirect(url_for('categories.categories'))
        try:
            cursor.execute(
                "INSERT INTO categories (name, description, user_id) VALUES (%s, %s, %s)",
                (name, description, current_user.id)
            )
            conn.commit()
            flash('Category added successfully')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect(url_for('categories.categories'))
    cursor.execute("SELECT id, name, description FROM categories WHERE user_id = %s ORDER BY name", (current_user.id,))
    all_categories = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('categories.html', categories=all_categories)


@bp.route('/categories/edit/<int:category_id>', methods=['GET', 'POST'])
@login_required
def edit_category(category_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM categories WHERE id = %s AND user_id = %s", (category_id, current_user.id))
    if cursor.fetchone() is None:
        cursor.close(); conn.close()
        abort(404)
    if request.method == 'POST':
        name = request.form['name'].strip()
        description = request.form.get('description', '').strip()
        if not name:
            cursor.close(); conn.close()
            flash('Name is required')
            return redirect(url_for('categories.edit_category', category_id=category_id))
        if len(name) > 50:
            cursor.close(); conn.close()
            flash('Name must be 50 characters or fewer')
            return redirect(url_for('categories.edit_category', category_id=category_id))
        try:
            cursor.execute(
                "UPDATE categories SET name=%s, description=%s WHERE id=%s AND user_id=%s",
                (name, description, category_id, current_user.id)
            )
            conn.commit()
            flash('Category updated successfully')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect(url_for('categories.categories'))
    cursor.execute("SELECT id, name, description FROM categories WHERE id = %s AND user_id = %s", (category_id, current_user.id))
    category = cursor.fetchone()
    cursor.close()
    conn.close()
    return render_template('edit_category.html', category=category)


@bp.route('/categories/delete/<int:category_id>', methods=['GET', 'POST'])
@login_required
def delete_category(category_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM categories WHERE id = %s AND user_id = %s", (category_id, current_user.id))
    if cursor.fetchone() is None:
        cursor.close(); conn.close()
        abort(404)
    if request.method == 'POST':
        try:
            cursor.execute("DELETE FROM categories WHERE id = %s AND user_id = %s", (category_id, current_user.id))
            conn.commit()
            flash('Category deleted')
        except Exception as e:
            if 'foreign key' in str(e).lower():
                flash('Cannot delete — this category is used by existing transactions or budgets')
            else:
                flash(f'Error: {e}')
            conn.rollback()
        cursor.close()
        conn.close()
        return redirect(url_for('categories.categories'))
    cursor.execute("SELECT id, name, description FROM categories WHERE id = %s AND user_id = %s", (category_id, current_user.id))
    category = cursor.fetchone()
    cursor.close()
    conn.close()
    return render_template('delete_category.html', category=category)
