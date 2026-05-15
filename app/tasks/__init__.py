"""Celery task package setup."""

from __future__ import annotations

import os
import sys

from celery import Celery
from flask import Flask


# Single Celery instance shared by Flask app, routes, and worker bootstrap.
celery = Celery(__name__)


def make_celery(app: Flask) -> Celery:
    """Create and configure Celery instance tied to Flask app context."""

    celery.main = app.import_name
    celery.conf.update(
        broker=app.config.get(
            "CELERY_BROKER_URL",
            os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/1"),
        ),
        backend=app.config.get(
            "CELERY_RESULT_BACKEND",
            os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/2"),
        ),
        include=[
            "app.tasks.agent_tasks",
            "app.tasks.scheduled_tasks",
            "app.tasks.maintenance",
        ],
    )

    celery.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="Asia/Kolkata",
        enable_utc=True,
        # Use solo pool on Python 3.14+ to bypass billiard prefork incompatibilities.
        worker_pool="solo" if sys.version_info >= (3, 14) else "prefork",
        task_track_started=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_routes={
            "app.tasks.agent_tasks.run_agent_task": {
                "queue": "default",
            },
            "app.tasks.scheduled_tasks.cron_execute_scheduled_job": {
                "queue": "default",
            },
            "app.tasks.maintenance.*": {
                "queue": "low",
            },
        },
        task_queues={
            "high": {"exchange": "high", "routing_key": "high"},
            "default": {"exchange": "default", "routing_key": "default"},
            "low": {"exchange": "low", "routing_key": "low"},
        },
        task_default_queue="default",
        beat_schedule={
            "cleanup-expired-outputs-daily": {
                "task": "app.tasks.maintenance.cleanup_expired_outputs",
                "schedule": 86400.0,
            },
            "send-weekly-digest": {
                "task": "app.tasks.maintenance.send_digest_emails",
                "schedule": 604800.0,
            },
            "execute-scheduled-jobs": {
                "task": "app.tasks.scheduled_tasks.check_and_execute_due_jobs",
                "schedule": 60.0,
            },
        },
    )

    class ContextTask(celery.Task):
        """Ensure Celery tasks execute with Flask application context."""

        def __call__(self, *args, **kwargs):  # type: ignore[override]
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    celery.set_default()
    return celery
