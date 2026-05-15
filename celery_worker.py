"""Celery worker bootstrap module."""

from app import create_app
from app.tasks import celery

app = create_app()
app.app_context().push()
