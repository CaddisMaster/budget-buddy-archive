import os
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import NamedTupleCursor

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
    """Yield a NamedTupleCursor, then always close the cursor + connection.

    Rows are namedtuples — read `row.amount` (positional `row[1]` still works).
    Every SELECT column therefore needs a unique, valid-identifier name; alias
    expressions and duplicate JOIN columns with AS. Pass commit=True for write
    paths (commits on a clean exit). Any exception rolls back and re-raises,
    so callers can `flash` the error as before.
    """
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=NamedTupleCursor)
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
