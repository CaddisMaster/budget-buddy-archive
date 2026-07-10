from collections import namedtuple

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort,
    make_response, current_app
)
from flask_login import login_required, current_user
from app.db import db_cursor
from app.helpers import is_htmx, hx_toast, GENERIC_ERROR

bp = Blueprint('categories', __name__)

# Hand-built rows for the edit error/re-render paths — field names mirror the
# "SELECT id, name, description" shape so templates read both identically.
CategoryRow = namedtuple('CategoryRow', 'id name description')


def _own_category_or_404(cursor, category_id):
    cursor.execute(
        "SELECT id, name, description FROM categories WHERE id = %s AND user_id = %s",
        (category_id, current_user.id),
    )
    row = cursor.fetchone()
    if row is None:
        abort(404)
    return row


@bp.route('/categories', methods=['GET', 'POST'])
@login_required
def categories():
    if request.method == 'POST':
        name = request.form['name'].strip()
        description = request.form.get('description', '').strip()
        error = None
        if not name:
            error = 'Name is required'
        elif len(name) > 50:
            error = 'Name must be 50 characters or fewer'
        if error:
            if is_htmx():
                return hx_toast(make_response('', 200), error, 'error')
            flash(error)
            return redirect(url_for('categories.categories'))
        try:
            with db_cursor(commit=True) as cursor:
                cursor.execute(
                    "INSERT INTO categories (name, description, user_id) "
                    "VALUES (%s, %s, %s) RETURNING id, name, description",
                    (name, description, current_user.id),
                )
                cat = cursor.fetchone()
        except Exception:
            current_app.logger.exception('create category failed')
            if is_htmx():
                return hx_toast(make_response('', 200), GENERIC_ERROR, 'error')
            flash(GENERIC_ERROR)
            return redirect(url_for('categories.categories'))
        if is_htmx():
            resp = make_response(render_template('partials/_category_row.html', cat=cat))
            return hx_toast(resp, 'Category added')
        flash('Category added successfully')
        return redirect(url_for('categories.categories'))

    with db_cursor() as cursor:
        cursor.execute(
            "SELECT id, name, description FROM categories WHERE user_id = %s ORDER BY name",
            (current_user.id,),
        )
        all_categories = cursor.fetchall()
    return render_template('categories.html', categories=all_categories)


@bp.route('/categories/<int:category_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_category(category_id):
    # Guard ownership up front (404 for missing/other-user) so the write path's
    # try/except below can't accidentally swallow the abort.
    with db_cursor() as cursor:
        cat = _own_category_or_404(cursor, category_id)

    if request.method == 'POST':
        name = request.form['name'].strip()
        description = request.form.get('description', '').strip()
        error = None
        if not name:
            error = 'Name is required'
        elif len(name) > 50:
            error = 'Name must be 50 characters or fewer'
        if error:
            return render_template('partials/_category_edit_row.html',
                                   cat=CategoryRow(category_id, name, description), error=error)
        try:
            with db_cursor(commit=True) as cursor:
                cursor.execute(
                    "UPDATE categories SET name=%s, description=%s WHERE id=%s AND user_id=%s",
                    (name, description, category_id, current_user.id),
                )
        except Exception:
            current_app.logger.exception('edit category failed')
            return render_template('partials/_category_edit_row.html',
                                   cat=CategoryRow(category_id, name, description), error=GENERIC_ERROR)
        resp = make_response(render_template('partials/_category_row.html',
                                             cat=CategoryRow(category_id, name, description)))
        return hx_toast(resp, 'Category updated')

    return render_template('partials/_category_edit_row.html', cat=cat)


@bp.route('/categories/<int:category_id>/row')
@login_required
def category_row(category_id):
    with db_cursor() as cursor:
        cat = _own_category_or_404(cursor, category_id)
    return render_template('partials/_category_row.html', cat=cat)


@bp.route('/categories/<int:category_id>', methods=['DELETE'])
@login_required
def delete_category(category_id):
    with db_cursor() as cursor:
        cat = _own_category_or_404(cursor, category_id)
    try:
        with db_cursor(commit=True) as cursor:
            cursor.execute(
                "DELETE FROM categories WHERE id = %s AND user_id = %s",
                (category_id, current_user.id),
            )
    except Exception as e:
        current_app.logger.exception('delete category failed')
        msg = ('Cannot delete — this category is used by existing transactions or budgets'
               if 'foreign key' in str(e).lower() else GENERIC_ERROR)
        resp = make_response(render_template('partials/_category_row.html', cat=cat))
        return hx_toast(resp, msg, 'error')
    return hx_toast(make_response('', 200), 'Category deleted')
