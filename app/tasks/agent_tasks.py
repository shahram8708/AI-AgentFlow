"""Celery task module for automation agent jobs."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from uuid import UUID

import redis
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from celery.utils.log import get_task_logger

from app.extensions import db
from app.models import AutomationTask

logger = get_task_logger(__name__)


@shared_task(
    bind=True,
    name="app.tasks.agent_tasks.run_agent_task",
    max_retries=3,
    default_retry_delay=5,
    soft_time_limit=870,
    time_limit=900,
    acks_late=True,
)
def run_agent_task(self, task_id: str) -> dict:
    """Execute an AI automation task in background Celery worker."""

    from app.services.agent_runner import execute_task

    start_time = time.time()
    logger.info("Starting agent task execution: task_id=%s", task_id)

    try:
        success = execute_task(task_id)

        duration = time.time() - start_time
        logger.info(
            "Task %s completed: success=%s, duration=%.1fs",
            task_id,
            success,
            duration,
        )

        return {
            "success": success,
            "task_id": task_id,
            "duration_seconds": round(duration, 1),
        }

    except SoftTimeLimitExceeded:
        logger.warning(
            "Task %s approaching time limit and attempting graceful shutdown",
            task_id,
        )

        try:
            task = AutomationTask.query.get(UUID(task_id))
            if task and task.status == "running":
                task.status = "failed"
                task.error_message = (
                    "Task exceeded maximum execution time (15 minutes). "
                    "Please try with a smaller scope or Quick depth level."
                )
                task.completed_at = datetime.utcnow()
                db.session.commit()
        except Exception:  # pylint: disable=broad-except
            db.session.rollback()

        try:
            redis_client = redis.Redis.from_url(
                os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            )
            redis_client.publish(
                f"task_log:{task_id}",
                json.dumps(
                    {
                        "type": "log",
                        "level": "failed",
                        "message": (
                            "Task timed out after 15 minutes. "
                            "Please try again with a narrower scope."
                        ),
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                ),
            )
            redis_client.close()
        except Exception:  # pylint: disable=broad-except
            pass

        return {
            "success": False,
            "task_id": task_id,
            "error": "timeout",
        }

    except Exception as exc:  # pylint: disable=broad-except
        duration = time.time() - start_time
        retry_delays = [5, 30, 120]
        current_retry = self.request.retries

        logger.error(
            "Task %s failed (attempt %s/3): %s",
            task_id,
            current_retry + 1,
            exc,
            exc_info=True,
        )

        if current_retry < self.max_retries:
            retry_delay = retry_delays[current_retry] if current_retry < len(retry_delays) else 120
            logger.info(
                "Retrying task %s in %ss (attempt %s)",
                task_id,
                retry_delay,
                current_retry + 2,
            )

            try:
                task = AutomationTask.query.get(UUID(task_id))
                if task:
                    task.status = "pending"
                    task.retry_count = current_retry + 1
                    task.error_message = f"Retry {current_retry + 1}: {str(exc)[:500]}"
                    db.session.commit()
            except Exception:  # pylint: disable=broad-except
                db.session.rollback()

            raise self.retry(exc=exc, countdown=retry_delay)

        logger.error(
            "Task %s permanently failed after %s retries",
            task_id,
            self.max_retries,
        )

        try:
            task = AutomationTask.query.get(UUID(task_id))
            if task:
                task.status = "failed"
                task.error_message = f"Failed after {self.max_retries} attempts: {str(exc)[:1000]}"
                task.completed_at = datetime.utcnow()
                db.session.commit()

                from app.services.notification_service import NotificationService

                NotificationService().notify_task_failed(task, task.error_message)
        except Exception:  # pylint: disable=broad-except
            db.session.rollback()

        return {
            "success": False,
            "task_id": task_id,
            "error": str(exc),
            "duration_seconds": round(duration, 1),
        }
