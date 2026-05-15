from app import app
from app.db import get_db_connection
from flask import render_template, request, redirect, url_for, flash

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/transactions/new', methods=['GET', 'POST'])
def new_transaction():
    if request.method == 'POST':
        amount = request.form['amount']
        description = request.form['description']
        transaction_date = request.form['transaction_date']
        category_id = request.form['category_id']
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO transactions (amount, description, transaction_date, category_id) VALUES (%s, %s, %s, %s)",
                (amount, description, transaction_date, category_id)
            )
            conn.commit()
            flash('Transaction added successfully')
        except Exception as e:
            flash(f'Error: {e}')
            if conn:
                conn.rollback()
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        return redirect(url_for('new_transaction'))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM categories ORDER BY name")
    all_categories = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('new_transaction.html', categories=all_categories)

@app.route('/categories', methods=['GET', 'POST'])
def categories():
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        name = request.form['name'].strip()
        description = request.form.get('description', '').strip()
        try:
            cursor.execute(
                "INSERT INTO categories (name, description) VALUES (%s, %s)",
                (name, description)
            )
            conn.commit()
            flash('Category added successfully')
        except Exception as e:
            flash(f'Error: {e}')
            conn.rollback()
        return redirect(url_for('categories'))
    cursor.execute("SELECT id, name, description FROM categories ORDER BY name")
    all_categories = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('categories.html', categories=all_categories)

@app.route('/transactions')
def transactions():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT t.id, t.amount, t.description, c.name, t.transaction_date
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        ORDER BY t.transaction_date DESC
    """)
    all_transactions = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('history.html', transactions=all_transactions)