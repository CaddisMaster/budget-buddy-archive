import os
import psycopg2
from dotenv import load_dotenv
from ingest import load_transactions
from clean import clean_transactions

load_dotenv()

def get_connection():
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT'),
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )

def insert_transactions(df):
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        sql = """
            INSERT INTO transactions (amount, description, transaction_date)
            VALUES (%s, %s, %s)
        """
        rows = [
            (row.amount, row.description, row.transaction_date)
            for row in df.itertuples(index=False)
        ]
        cursor.executemany(sql, rows)
        conn.commit()
        print(f"Inserted {len(rows)} rows successfully.")
    except Exception as e:
        print(f"Insert failed: {e}")
        if conn:
            conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

if __name__ == "__main__":
    df = load_transactions()
    df = clean_transactions(df)
    insert_transactions(df)