import os
import hmac
from flask import Flask, redirect, url_for
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_bcrypt import Bcrypt

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')

limiter = Limiter(
  get_remote_address,
  app=app,
  default_limits=["60 per minute"],
  storage_uri="memory://"
)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

bcrypt = Bcrypt(app)

from app.models import User

@login_manager.user_loader
def load_user(user_id):
  return User.get_by_id(int(user_id))

from app import routes