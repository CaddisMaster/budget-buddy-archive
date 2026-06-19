import os
from contextlib import contextmanager

import psycopg2

def get_db_connection():
    conn = psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT'),
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )
    return conn


@contextmanager
def db_cursor(commit=False):
    """Yield a cursor, then always close the cursor + connection.

    Pass commit=True for write paths (commits on a clean exit). Any exception
    rolls back and re-raises, so callers can `flash` the error as before. This
    retires the repetitive `cursor.close(); conn.close()` dance in newer code.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        yield cursor
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
