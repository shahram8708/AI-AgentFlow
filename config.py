"""Application configuration classes."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def _normalize_database_url(raw_url: str | None) -> str:
    """Return a valid SQLAlchemy database URL from env input."""

    default_url = "sqlite:///agentflow.db"
    if raw_url is None:
        return default_url

    url = raw_url.strip().strip("\"'")
    if not url:
        return default_url

    if url.startswith("postgres://"):
        return f"postgresql://{url[len('postgres://'):]}"

    if "://" in url:
        return url

    if url == ":memory:":
        return "sqlite:///:memory:"

    normalized_path = Path(os.path.expanduser(url)).as_posix()
    return f"sqlite:///{normalized_path}"


class BaseConfig:
    """Base configuration loaded from environment variables."""

    load_dotenv()

    SECRET_KEY = os.environ.get("SECRET_KEY")
    FLASK_ENV = os.environ.get("FLASK_ENV", "development")
    APP_VERSION = os.environ.get("APP_VERSION", "1.0.0")

    SQLALCHEMY_DATABASE_URI = _normalize_database_url(os.environ.get("DATABASE_URL"))
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    CACHE_TYPE = "redis"
    CACHE_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    CACHE_DEFAULT_TIMEOUT = int(os.environ.get("CACHE_DEFAULT_TIMEOUT", "300"))

    RATELIMIT_STORAGE_URI = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    RATELIMIT_STRATEGY = "fixed-window"
    RATELIMIT_HEADERS_ENABLED = True

    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.sendgrid.net")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "True").lower() == "true"
    MAIL_USE_SSL = os.environ.get("MAIL_USE_SSL", "False").lower() == "true"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "noreply@example.com")

    CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/1")
    CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")

    GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

    RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID")
    RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET")
    RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET")

    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "uploads")
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", "16777216"))

    OUTPUT_RETENTION_DEFAULT_DAYS = int(
        os.environ.get("OUTPUT_RETENTION_DEFAULT_DAYS", "30")
    )
    FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5000")

    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PREFERRED_URL_SCHEME = "http"


class DevelopmentConfig(BaseConfig):
    """Configuration for local development."""

    DEBUG = True
    TESTING = False
    SQLALCHEMY_ECHO = False


class TestingConfig(BaseConfig):
    """Configuration for test execution."""

    DEBUG = False
    TESTING = True
    SQLALCHEMY_ECHO = False
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://username:password@localhost:5432/ai_platform_test_db",
    )
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False


class ProductionConfig(BaseConfig):
    """Configuration for production deployment."""

    DEBUG = False
    TESTING = False
    SQLALCHEMY_ECHO = False
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PREFERRED_URL_SCHEME = "https"
