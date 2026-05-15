import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

conn = None
cursor = None

try:
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD")
    )
    cursor = conn.cursor()
    cursor.execute("SELECT version();")
    result = cursor.fetchall()
    print("Connected succcessfully")
    print(result)
except Exception as e:
    print(f"Connection failed: {e}")
finally:
    if cursor:
        cursor.close()
    if conn:
        conn.close()