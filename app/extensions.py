"""Flask extension instances used by the application factory."""

from celery import Celery
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_mail import Mail
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"

mail = Mail()
limiter = Limiter(key_func=get_remote_address)
cache = Cache()
migrate = Migrate()
csrf = CSRFProtect()
celery = Celery(__name__)
