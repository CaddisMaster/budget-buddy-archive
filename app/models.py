from flask_login import UserMixin
from app.db import db_cursor

class User(UserMixin):
    def __init__(self, id, username, password_hash, is_admin):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.is_admin = is_admin

    @staticmethod
    def get_by_id(user_id):
        with db_cursor() as cursor:
            cursor.execute(
                "SELECT id, username, password_hash, is_admin FROM users WHERE id = %s",
                (user_id,)
            )
            row = cursor.fetchone()
        if row:
            return User(row[0], row[1], row[2], row[3])
        return None

    @staticmethod
    def get_by_username(username):
        with db_cursor() as cursor:
            cursor.execute(
                "SELECT id, username, password_hash, is_admin FROM users WHERE username = %s",
                (username,)
            )
            row = cursor.fetchone()
        if row:
            return User(row[0], row[1], row[2], row[3])
        return None
