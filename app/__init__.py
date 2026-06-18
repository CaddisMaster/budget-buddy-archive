import os
import hmac
from flask import Flask, redirect, url_for
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_bcrypt import Bcrypt
from flask_wtf.csrf import CSRFProtect

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')

csrf = CSRFProtect(app)

limiter = Limiter(
  get_remote_address,
  app=app,
  default_limits=["60 per minute"],
  storage_uri="memory://"
)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'

bcrypt = Bcrypt(app)

from app.models import User

@login_manager.user_loader
def load_user(user_id):
  return User.get_by_id(int(user_id))

from app.blueprints import (
  auth, main, transactions, categories, accounts, budgets, analytics, admin,
  transfers, goals
)

app.register_blueprint(auth.bp)
app.register_blueprint(main.bp)
app.register_blueprint(transactions.bp)
app.register_blueprint(categories.bp)
app.register_blueprint(accounts.bp)
app.register_blueprint(budgets.bp)
app.register_blueprint(analytics.bp)
app.register_blueprint(admin.bp)
app.register_blueprint(transfers.bp)
app.register_blueprint(goals.bp)