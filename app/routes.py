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
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO transactions (amount, description, transaction_date) VALUES (%s, %s, %s)",
                (amount, description, transaction_date)
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
    return render_template('new_transaction.html')