import os
import hmac
from flask import Flask, request, Response
from dotenv import load_dotenv
from functools import wraps
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["60 per minute"],
    storage_uri="memory://"
)

def check_auth(username, password):
    correct_username = os.getenv('APP_USERNAME', '')
    correct_password = os.getenv('APP_PASSWORD', '')
    username_valid = hmac.compare_digest(username, correct_username)
    password_valid = hmac.compare_digest(password, correct_password)
    return username_valid and password_valid

def require_auth():
    auth = request.authorization
    if not auth or not check_auth(auth.username, auth.password):
        return Response(
            'Authentication required',
            401,
            {'WWW-Authenticate': 'Basic realm="Budget Buddy"'}
        )

@app.before_request
def before_request():
    result = require_auth()
    if result:
        return result

from app import routes