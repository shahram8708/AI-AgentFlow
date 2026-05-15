"""Celery task module for maintenance operations."""

from __future__ import annotations

from datetime import datetime, timedelta

from celery import shared_task
from celery.utils.log import get_task_logger

from app.extensions import db
from app.models import AutomationTask, Organization, Plan, TaskOutput, UsageRecord, User
from app.services.auth_service import AuthService
from app.services.email_service import EmailService
from app.services.file_service import file_service

logger = get_task_logger(__name__)


@shared_task(name="app.tasks.maintenance.cleanup_expired_outputs")
def cleanup_expired_outputs() -> dict[str, int]:
    """Delete or soft delete outputs that exceed plan based retention windows."""

    organizations = Organization.query.filter_by(is_deleted=False).all()
    deleted_count = 0
    org_count = 0

    for org in organizations:
        plan = Plan.query.get(org.plan_id) if org.plan_id else None
        retention_days = int(plan.output_retention_days) if plan else 30

        if retention_days <= 0:
            continue

        org_count += 1
        cutoff_date = datetime.utcnow() - timedelta(days=retention_days)

        expired_outputs = (
            TaskOutput.query.join(AutomationTask, TaskOutput.task_id == AutomationTask.id)
            .filter(
                AutomationTask.org_id == org.id,
                TaskOutput.created_at < cutoff_date,
                TaskOutput.is_deleted.is_(False),
            )
            .all()
        )

        for output in expired_outputs:
            if output.file_path:
                file_service.delete_file(output.file_path)

            output.is_deleted = True
            output.deleted_at = datetime.utcnow()
            deleted_count += 1

    db.session.commit()

    logger.info(
        "Cleanup complete. Soft-deleted %s outputs across %s organizations.",
        deleted_count,
        org_count,
    )
    return {
        "deleted_outputs": deleted_count,
        "orgs_processed": org_count,
    }


@shared_task(name="app.tasks.maintenance.send_digest_emails")
def send_digest_emails() -> dict[str, int]:
    """Send weekly usage digest emails to active users with recent activity."""

    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    now_utc = datetime.utcnow()
    month_start = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    users = (
        User.query.join(AutomationTask, AutomationTask.user_id == User.id)
        .filter(
            User.is_active.is_(True),
            AutomationTask.created_at >= seven_days_ago,
        )
        .distinct()
        .all()
    )

    auth_service = AuthService()
    email_service = EmailService()
    sent_count = 0

    from app.services.agent_runner import TASK_REGISTRY

    for user in users:
        org = auth_service.get_user_org(user.id)
        if org is None or org.is_deleted:
            continue

        plan = Plan.query.get(org.plan_id) if org.plan_id else None

        recent_tasks = (
            AutomationTask.query.filter(
                AutomationTask.user_id == user.id,
                AutomationTask.org_id == org.id,
                AutomationTask.created_at >= seven_days_ago,
            )
            .order_by(AutomationTask.created_at.desc())
            .all()
        )

        if not recent_tasks:
            continue

        tasks_completed = sum(1 for task in recent_tasks if task.status == "done")
        tasks_failed = sum(1 for task in recent_tasks if task.status == "failed")

        outputs_generated = (
            TaskOutput.query.join(AutomationTask, TaskOutput.task_id == AutomationTask.id)
            .filter(
                AutomationTask.user_id == user.id,
                AutomationTask.org_id == org.id,
                TaskOutput.is_deleted.is_(False),
                TaskOutput.created_at >= seven_days_ago,
            )
            .count()
        )

        category_counts: dict[str, int] = {}
        for task in recent_tasks:
            config = TASK_REGISTRY.get(task.task_type, {})
            category = str(config.get("category_display") or config.get("category") or "General")
            category_counts[category] = category_counts.get(category, 0) + 1

        most_used_category = "General"
        if category_counts:
            most_used_category = max(category_counts, key=category_counts.get)

        quota_limit = int(plan.task_quota_monthly) if plan else 10
        quota_used = (
            UsageRecord.query.filter(
                UsageRecord.org_id == org.id,
                UsageRecord.usage_type == "task_run",
                UsageRecord.recorded_at >= month_start,
            ).count()
        )

        if quota_limit > 0:
            quota_percent = min(round((quota_used / quota_limit) * 100, 1), 100.0)
        else:
            quota_percent = 0.0

        context = {
            "first_name": user.first_name,
            "tasks_completed": tasks_completed,
            "tasks_failed": tasks_failed,
            "outputs_generated": outputs_generated,
            "most_used_category": most_used_category,
            "time_saved_minutes": tasks_completed * 30,
            "quota_used": quota_used,
            "quota_limit": quota_limit,
            "quota_percent": quota_percent,
            "dashboard_url": "/tasks/new",
            "plan_name": plan.name if plan else "Free",
        }

        sent = email_service.send_generic_email(
            user.email,
            "Your Weekly AgentFlow Summary",
            "emails/weekly_digest.html",
            context,
        )
        if sent:
            sent_count += 1

    logger.info("Sent digest emails to %s users", sent_count)
    return {"sent_count": sent_count}
