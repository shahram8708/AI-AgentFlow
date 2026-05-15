"""Celery task module for scheduled workflow execution."""

from __future__ import annotations

from datetime import datetime
import os
from typing import Any
from uuid import UUID

from celery import shared_task
from celery.utils.log import get_task_logger
import redis

from app.extensions import db
from app.models import AuditLog, AutomationTask, ScheduledJob, Workflow
from app.routes.schedules import recalculate_next_run
from app.services.notification_service import NotificationService

logger = get_task_logger(__name__)

LOCK_KEY = "schedule_check_lock"


def _redis_client() -> redis.Redis:
    """Create Redis client for distributed scheduling lock."""

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(redis_url)


def _task_input_from_step(step_config: dict[str, Any]) -> dict[str, Any]:
    """Build task input payload from workflow step configuration."""

    raw_inputs = step_config.get("input_json")
    if raw_inputs is None and isinstance(step_config.get("input"), dict):
        raw_inputs = step_config.get("input")

    step_inputs = raw_inputs if isinstance(raw_inputs, dict) else {}
    workflow_defaults = step_config.get("defaults") if isinstance(step_config.get("defaults"), dict) else {}

    return {**workflow_defaults, **step_inputs}


def _enqueue_workflow_step(
    job: ScheduledJob,
    workflow: Workflow,
    notification_payloads: list[dict[str, Any]],
) -> AutomationTask | None:
    """Create queued AutomationTask for the first workflow step and enqueue it."""

    from app.services.agent_runner import get_task_config
    from app.tasks.agent_tasks import run_agent_task

    workflow_steps = workflow.steps_json if isinstance(workflow.steps_json, list) else []
    if not workflow_steps:
        logger.warning("Workflow %s has no steps for schedule %s", workflow.id, job.id)
        job.last_run_at = datetime.utcnow()
        job.last_run_status = "failed"
        return None

    first_step = workflow_steps[0] if isinstance(workflow_steps[0], dict) else {}
    task_type = str(first_step.get("task_type") or "").strip()
    if not task_type:
        logger.warning("Workflow %s first step missing task_type for schedule %s", workflow.id, job.id)
        job.last_run_at = datetime.utcnow()
        job.last_run_status = "failed"
        return None

    try:
        task_config = get_task_config(task_type)
    except KeyError:
        logger.warning(
            "Workflow %s has invalid task_type %s for schedule %s",
            workflow.id,
            task_type,
            job.id,
        )
        job.last_run_at = datetime.utcnow()
        job.last_run_status = "failed"
        return None

    new_task = AutomationTask(
        org_id=job.org_id,
        project_id=workflow.project_id,
        workflow_id=job.workflow_id,
        user_id=job.created_by,
        task_type=task_type,
        task_name=f"Scheduled: {workflow.name}",
        input_json=_task_input_from_step(first_step),
        status="pending",
        priority="low",
        timeout_seconds=int(task_config.get("timeout_seconds", 300) or 300),
    )
    db.session.add(new_task)
    db.session.flush()

    result = run_agent_task.apply_async(args=[str(new_task.id)], queue="low", countdown=0)
    new_task.celery_task_id = result.id

    job.last_run_at = datetime.utcnow()
    job.last_run_status = "queued"
    recalculate_next_run(job)

    db.session.add(
        AuditLog(
            org_id=job.org_id,
            user_id=job.created_by,
            action="schedule.triggered",
            resource_type="scheduled_job",
            resource_id=str(job.id),
            extra_json={
                "job_id": str(job.id),
                "workflow_name": workflow.name,
                "task_id": str(new_task.id),
            },
        )
    )

    notification_payloads.append(
        {
            "user_id": job.created_by,
            "org_id": job.org_id,
            "notification_type": "system",
            "title": "Scheduled Task Started",
            "message": f'Scheduled workflow "{workflow.name}" has been triggered.',
            "action_url": f"/tasks/{new_task.id}",
        }
    )

    return new_task


@shared_task(name="app.tasks.scheduled_tasks.check_and_execute_due_jobs")
def check_and_execute_due_jobs() -> dict[str, int]:
    """Check active schedules, enqueue due jobs, and recalculate next runs."""

    redis_client = _redis_client()
    lock_acquired = False

    try:
        lock_acquired = bool(redis_client.set(LOCK_KEY, "1", nx=True, ex=55))
        if not lock_acquired:
            logger.info("Skipping scheduled job check. Another worker holds the lock.")
            return {"checked": 0, "enqueued": 0}

        all_jobs_count = ScheduledJob.query.filter(ScheduledJob.is_active.is_(True)).count()
        due_jobs = ScheduledJob.query.filter(
            ScheduledJob.is_active.is_(True),
            ScheduledJob.next_run_at <= datetime.utcnow(),
        ).all()

        enqueued_count = 0
        notification_payloads: list[dict[str, Any]] = []

        for job in due_jobs:
            try:
                with db.session.begin_nested():
                    workflow = Workflow.query.get(job.workflow_id)
                    if workflow is None or workflow.is_deleted:
                        logger.warning(
                            "Deactivating scheduled job %s due to missing workflow %s",
                            job.id,
                            job.workflow_id,
                        )
                        job.is_active = False
                        job.last_run_status = "failed"
                        job.last_run_at = datetime.utcnow()
                        continue

                    created_task = _enqueue_workflow_step(job, workflow, notification_payloads)
                    if created_task is not None:
                        enqueued_count += 1
            except Exception as exc:  # pylint: disable=broad-except
                logger.error(
                    "Failed to process scheduled job %s: %s",
                    job.id,
                    exc,
                    exc_info=True,
                )
                continue

        db.session.commit()

        # Notifications are sent after the primary transaction commit.
        notification_service = NotificationService()
        for payload in notification_payloads:
            try:
                notification_service.create_notification(**payload)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("Failed to create scheduled notification: %s", exc)

        logger.info(
            "Scheduled job check: %s due, %s enqueued",
            len(due_jobs),
            enqueued_count,
        )
        return {"checked": all_jobs_count, "enqueued": enqueued_count}
    except Exception as exc:  # pylint: disable=broad-except
        db.session.rollback()
        logger.error("Scheduled job checker failed: %s", exc, exc_info=True)
        return {"checked": 0, "enqueued": 0}
    finally:
        if lock_acquired:
            try:
                redis_client.delete(LOCK_KEY)
            except Exception:
                logger.warning("Unable to release schedule lock key")
        try:
            redis_client.close()
        except Exception:
            pass


@shared_task(name="app.tasks.scheduled_tasks.cron_execute_scheduled_job")
def cron_execute_scheduled_job(job_id: str) -> dict[str, Any]:
    """Manually trigger a specific scheduled job by UUID."""

    if not job_id:
        logger.error("Scheduled job id is required")
        return {"error": "Job not found"}

    try:
        job_uuid = UUID(str(job_id))
    except (TypeError, ValueError):
        logger.error("Invalid scheduled job id: %s", job_id)
        return {"error": "Job not found"}

    job = ScheduledJob.query.get(job_uuid)
    if job is None:
        logger.error("Scheduled job not found: %s", job_id)
        return {"error": "Job not found"}

    workflow = Workflow.query.get(job.workflow_id)
    if workflow is None or workflow.is_deleted:
        logger.error("Workflow missing for scheduled job %s", job_id)
        return {"error": "Workflow not found"}

    notification_payloads: list[dict[str, Any]] = []

    try:
        created_task = _enqueue_workflow_step(job, workflow, notification_payloads)
        if created_task is None:
            db.session.commit()
            return {"error": "Workflow has no executable steps"}

        db.session.commit()

        notification_service = NotificationService()
        for payload in notification_payloads:
            try:
                notification_service.create_notification(**payload)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("Failed to create manual trigger notification: %s", exc)

        return {
            "success": True,
            "task_id": str(created_task.id),
        }
    except Exception as exc:  # pylint: disable=broad-except
        db.session.rollback()
        logger.error("Manual schedule execution failed for %s: %s", job_id, exc, exc_info=True)
        return {"error": str(exc)}
