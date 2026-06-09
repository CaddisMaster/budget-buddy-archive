from flask_login import UserMixin
from app.db import get_db_connection

class User(UserMixin):
  def __init__(self, id, email, password_hash, is_admin):
    self.id = id
    self.email = email
    self.password_hash = password_hash
    self.is_admin = is_admin

  @staticmethod
  def get_by_id(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
      "SELECT id, email, password_hash, is_admin FROM users WHERE id = %s",
      (user_id,)
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
      return User(row[0], row[1], row[2], row[3])
    return None

  @staticmethod
  def get_by_email(email):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
      "SELECT id, email, password_hash, is_admin FROM users WHERE email = %s",
      (email,)
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
      return User(row[0], row[1], row[2], row[3])
    return None