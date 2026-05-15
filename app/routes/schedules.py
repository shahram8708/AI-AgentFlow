"""Schedules blueprint routes and cron helper functions."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from croniter import croniter
import pytz
from flask import Blueprint, abort, flash, g, redirect, render_template, request
from flask_login import current_user
from sqlalchemy.orm import joinedload
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.forms.workflow import ScheduleForm
from app.models import AuditLog, AutomationTask, ScheduledJob, Workflow
from app.utils.decorators import login_required, org_required
from app.utils.response_helpers import error_response, success_response
from app.utils.validators import validate_cron_expression, validate_uuid

schedules_bp = Blueprint("schedules", __name__)

DEFAULT_SCHEDULE_CRONS = {
    "daily": "0 9 * * *",
    "weekly": "0 9 * * 1",
    "monthly": "0 9 1 * *",
}

DAY_LABELS = {
    "0": "Sunday",
    "1": "Monday",
    "2": "Tuesday",
    "3": "Wednesday",
    "4": "Thursday",
    "5": "Friday",
    "6": "Saturday",
    "7": "Sunday",
}


def _is_json_request() -> bool:
    """Return True when route should emit JSON responses."""

    requested_with = (request.headers.get("X-Requested-With") or "").strip().lower()
    return (
        request.is_json
        or request.path.startswith("/api")
        or request.accept_mimetypes.best == "application/json"
        or requested_with == "xmlhttprequest"
    )


def _as_bool(value: Any) -> bool:
    """Convert common truthy and falsy input values to bool."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _ordinal(value: int) -> str:
    """Return ordinal number string for day values."""

    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def _format_clock(hour_str: str, minute_str: str) -> str:
    """Format cron hour and minute into human readable 12 hour format."""

    if not hour_str.isdigit() or not minute_str.isdigit():
        return ""

    hour = int(hour_str)
    minute = int(minute_str)
    period = "AM" if hour < 12 else "PM"
    display_hour = hour % 12
    if display_hour == 0:
        display_hour = 12
    return f"{display_hour}:{minute:02d} {period}"


def _to_utc_naive(value: datetime | None) -> datetime | None:
    """Normalize timezone aware datetimes to naive UTC for database storage."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(pytz.utc).replace(tzinfo=None)


def _generate_cron_from_type(schedule_type: str) -> str:
    """Generate a default cron expression from schedule type."""

    normalized = (schedule_type or "daily").strip().lower()
    return DEFAULT_SCHEDULE_CRONS.get(normalized, DEFAULT_SCHEDULE_CRONS["daily"])


def _workflow_choices() -> list[tuple[str, str]]:
    """Return schedule workflow select choices for current org."""

    workflows = (
        Workflow.query.filter_by(org_id=g.org.id, is_deleted=False)
        .order_by(Workflow.updated_at.desc())
        .all()
    )
    choices: list[tuple[str, str]] = [("", "Select workflow")]
    for workflow in workflows:
        step_count = len(workflow.steps_json) if isinstance(workflow.steps_json, list) else 0
        choices.append((str(workflow.id), f"{workflow.name} ({step_count} steps)"))
    return choices


def _schedule_form(payload: dict[str, Any] | None = None) -> ScheduleForm:
    """Build ScheduleForm for form submissions and JSON payloads."""

    if payload is not None:
        form = ScheduleForm(formdata=MultiDict(payload), meta={"csrf": False})
    else:
        form = ScheduleForm()

    form.workflow_id.choices = _workflow_choices()
    return form


def _get_job(job_id: str) -> ScheduledJob | None:
    """Load scheduled job by UUID scoped to current org."""

    if not validate_uuid(job_id):
        return None

    return ScheduledJob.query.filter_by(id=UUID(job_id), org_id=g.org.id).first()


def get_cron_description(cron_expression: str) -> str:
    """Convert cron expressions into concise human readable descriptions."""

    expression = str(cron_expression or "").strip()
    if not expression:
        return "Invalid schedule"

    parts = expression.split()
    if len(parts) != 5:
        return "Invalid schedule"

    if expression == "0 9 * * *":
        return "Daily at 9:00 AM"
    if expression == "0 9 * * 1":
        return "Every Monday at 9:00 AM"
    if expression == "0 9 1 * *":
        return "1st of every month at 9:00 AM"
    if expression == "0 9 * * 1-5":
        return "Weekdays at 9:00 AM"
    if expression == "0 0 1 1 *":
        return "Annually on January 1st at midnight"

    minute, hour, day_of_month, month, day_of_week = parts

    if minute.startswith("*/") and hour == "*" and day_of_month == "*" and month == "*" and day_of_week == "*":
        interval = minute[2:]
        if interval.isdigit() and int(interval) > 0:
            return f"Every {int(interval)} minutes"

    if hour.startswith("*/") and minute == "0" and day_of_month == "*" and month == "*" and day_of_week == "*":
        interval = hour[2:]
        if interval.isdigit() and int(interval) > 0:
            return f"Every {int(interval)} hours"

    clock_label = _format_clock(hour, minute)
    if day_of_week in DAY_LABELS and day_of_month == "*" and month == "*" and clock_label:
        return f"Every {DAY_LABELS[day_of_week]} at {clock_label}"

    if day_of_month.isdigit() and month == "*" and day_of_week == "*" and clock_label:
        return f"{_ordinal(int(day_of_month))} of every month at {clock_label}"

    try:
        if validate_cron_expression(expression):
            croniter(expression, datetime.utcnow())
            return f"Custom: {expression}"
    except Exception:
        return "Invalid schedule"

    return "Invalid schedule"


def get_next_n_runs(cron_expression: str, timezone: str, n: int = 5) -> list[datetime]:
    """Calculate the next N run timestamps using croniter and timezone."""

    try:
        tz = pytz.timezone(timezone)
    except Exception:
        tz = pytz.timezone("Asia/Kolkata")

    try:
        base_time = datetime.now(tz=tz)
        cron = croniter(cron_expression, base_time)
        runs: list[datetime] = []
        for _ in range(max(n, 0)):
            next_run = cron.get_next(datetime)
            if next_run.tzinfo is None:
                next_run = tz.localize(next_run)
            runs.append(next_run)
        return runs
    except Exception:
        return []


def recalculate_next_run(job: ScheduledJob) -> datetime | None:
    """Recalculate and assign next_run_at for a ScheduledJob instance."""

    runs = get_next_n_runs(job.cron_expression, job.timezone, 1)
    job.next_run_at = _to_utc_naive(runs[0]) if runs else None
    return job.next_run_at


@schedules_bp.get("/schedules")
@login_required
@org_required
def schedules_home():
    """Render scheduled tasks manager page."""

    jobs = (
        ScheduledJob.query.options(joinedload(ScheduledJob.workflow))
        .filter_by(org_id=g.org.id)
        .order_by(ScheduledJob.created_at.desc())
        .all()
    )

    for job in jobs:
        setattr(job, "cron_description", get_cron_description(job.cron_expression))

    form = _schedule_form()

    return render_template(
        "app/schedules.html",
        jobs=jobs,
        schedule_form=form,
        active_schedule_count=sum(1 for job in jobs if job.is_active),
        cron_description_fn=get_cron_description,
    )


@schedules_bp.get("/schedules/new")
@login_required
@org_required
def schedules_new_redirect():
    """Redirect legacy schedule creation route to list page modal."""

    return redirect("/schedules")


@schedules_bp.post("/schedules")
@login_required
@org_required
def create_schedule():
    """Create a new scheduled workflow execution job."""

    payload = request.get_json(silent=True) if request.is_json else None
    if payload is not None and not isinstance(payload, dict):
        payload = {}

    form = _schedule_form(payload=payload)
    if not form.validate():
        errors = {field: messages for field, messages in form.errors.items()}
        if _is_json_request():
            return error_response("Please fix form errors.", status=400, errors=errors)
        flash("Please correct schedule form errors.", "danger")
        return redirect("/schedules")

    workflow_id_str = str(form.workflow_id.data or "").strip()
    if not validate_uuid(workflow_id_str):
        return error_response("Invalid workflow selected.", status=400)

    workflow = Workflow.query.filter_by(
        id=UUID(workflow_id_str),
        org_id=g.org.id,
        is_deleted=False,
    ).first()
    if workflow is None:
        return error_response("Workflow not found.", status=404)

    workflow_steps = workflow.steps_json if isinstance(workflow.steps_json, list) else []
    if not workflow_steps:
        return error_response("Cannot schedule a workflow with no steps.", status=400)

    schedule_type = str(form.schedule_type.data or "daily").strip().lower()
    cron_expression = str(form.cron_expression.data or "").strip()

    if schedule_type != "custom" and not cron_expression:
        cron_expression = _generate_cron_from_type(schedule_type)

    if not validate_cron_expression(cron_expression):
        return error_response("Invalid cron expression.", status=400)

    timezone_name = str(form.timezone.data or "Asia/Kolkata").strip()
    next_runs = get_next_n_runs(cron_expression, timezone_name, 1)
    if not next_runs:
        return error_response("Could not calculate next run time.", status=400)

    next_run_local = next_runs[0]
    next_run_at = _to_utc_naive(next_run_local)

    notify_on_completion = _as_bool(
        payload.get("notify_on_completion") if payload is not None else form.notify_on_completion.data
    )

    job = ScheduledJob(
        org_id=g.org.id,
        workflow_id=workflow.id,
        name=str(form.name.data or "").strip()[:255],
        cron_expression=cron_expression,
        timezone=timezone_name,
        next_run_at=next_run_at,
        is_active=True,
        created_by=current_user.id,
    )

    try:
        db.session.add(job)
        db.session.flush()

        db.session.add(
            AuditLog(
                org_id=g.org.id,
                user_id=current_user.id,
                action="schedule.created",
                resource_type="scheduled_job",
                resource_id=str(job.id),
                ip_address=request.remote_addr,
                user_agent=(request.user_agent.string[:500] if request.user_agent else None),
                extra_json={
                    "workflow_id": str(workflow.id),
                    "cron_expression": cron_expression,
                    "timezone": timezone_name,
                    "notify_on_completion": notify_on_completion,
                },
            )
        )

        db.session.commit()
    except Exception:
        db.session.rollback()
        return error_response("Could not create schedule.", status=500)

    run_label = next_run_local.strftime("%d %b %Y, %I:%M %p %Z")
    flash(f"Schedule created! First run: {run_label}", "success")

    if _is_json_request():
        return success_response({"schedule_id": str(job.id)})

    return redirect("/schedules")


@schedules_bp.get("/schedules/<job_id>")
@login_required
@org_required
def schedule_detail(job_id: str):
    """Return schedule details payload for modal rendering."""

    job = _get_job(job_id)
    if job is None:
        abort(404)

    workflow = Workflow.query.filter_by(
        id=job.workflow_id,
        org_id=g.org.id,
        is_deleted=False,
    ).first()

    run_history = (
        AutomationTask.query.filter(
            AutomationTask.workflow_id == job.workflow_id,
            AutomationTask.task_name.ilike("%Scheduled%"),
        )
        .order_by(AutomationTask.created_at.desc())
        .limit(10)
        .all()
    )

    next_runs = get_next_n_runs(job.cron_expression, job.timezone, 5)

    return success_response(
        {
            "job": {
                "id": str(job.id),
                "name": job.name,
                "workflow_id": str(job.workflow_id),
                "workflow_name": workflow.name if workflow else "Unknown Workflow",
                "cron_expression": job.cron_expression,
                "cron_description": get_cron_description(job.cron_expression),
                "timezone": job.timezone,
                "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None,
                "last_run_at": job.last_run_at.isoformat() if job.last_run_at else None,
                "last_run_status": job.last_run_status,
                "is_active": job.is_active,
            },
            "next_runs": [run.isoformat() for run in next_runs],
            "run_history": [
                {
                    "task_id": str(task.id),
                    "status": task.status,
                    "created_at": task.created_at.isoformat() if task.created_at else None,
                    "started_at": task.started_at.isoformat() if task.started_at else None,
                    "completed_at": task.completed_at.isoformat() if task.completed_at else None,
                    "task_name": task.task_name,
                }
                for task in run_history
            ],
        }
    )


@schedules_bp.put("/schedules/<job_id>")
@login_required
@org_required
def update_schedule(job_id: str):
    """Update schedule configuration and recompute next run when needed."""

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return error_response("Invalid JSON payload.", status=400)

    job = _get_job(job_id)
    if job is None:
        return error_response("Schedule not found.", status=404)

    before_json = {
        "name": job.name,
        "cron_expression": job.cron_expression,
        "timezone": job.timezone,
        "is_active": job.is_active,
    }

    name = payload.get("name")
    if name is not None:
        cleaned_name = str(name).strip()
        if len(cleaned_name) < 2:
            return error_response("Schedule name must be at least 2 characters.", status=400)
        job.name = cleaned_name[:255]

    schedule_type = str(payload.get("schedule_type") or "").strip().lower()
    cron_expression = str(payload.get("cron_expression") or "").strip()

    if not cron_expression and schedule_type and schedule_type != "custom":
        cron_expression = _generate_cron_from_type(schedule_type)

    if cron_expression:
        if not validate_cron_expression(cron_expression):
            return error_response("Invalid cron expression.", status=400)
        job.cron_expression = cron_expression

    timezone_name = payload.get("timezone")
    if timezone_name is not None:
        timezone_name = str(timezone_name).strip()
        try:
            pytz.timezone(timezone_name)
        except Exception:
            return error_response("Invalid timezone.", status=400)
        job.timezone = timezone_name

    notify_on_completion = _as_bool(payload.get("notify_on_completion")) if "notify_on_completion" in payload else None

    was_active = bool(job.is_active)
    if "is_active" in payload:
        job.is_active = _as_bool(payload.get("is_active"))

    cron_changed = "cron_expression" in payload or (schedule_type and schedule_type != "custom")
    timezone_changed = "timezone" in payload
    should_recalculate = job.is_active and (cron_changed or timezone_changed or not was_active)

    if should_recalculate:
        recalculate_next_run(job)

    try:
        db.session.add(
            AuditLog(
                org_id=g.org.id,
                user_id=current_user.id,
                action="schedule.updated",
                resource_type="scheduled_job",
                resource_id=str(job.id),
                ip_address=request.remote_addr,
                user_agent=(request.user_agent.string[:500] if request.user_agent else None),
                before_json=before_json,
                after_json={
                    "name": job.name,
                    "cron_expression": job.cron_expression,
                    "timezone": job.timezone,
                    "is_active": job.is_active,
                },
                extra_json={"notify_on_completion": notify_on_completion},
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        return error_response("Could not update schedule.", status=500)

    return success_response(
        {
            "updated": True,
            "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None,
        }
    )


@schedules_bp.delete("/schedules/<job_id>")
@login_required
@org_required
def delete_schedule(job_id: str):
    """Delete schedule record permanently."""

    job = _get_job(job_id)
    if job is None:
        return error_response("Schedule not found.", status=404)

    try:
        db.session.add(
            AuditLog(
                org_id=g.org.id,
                user_id=current_user.id,
                action="schedule.deleted",
                resource_type="scheduled_job",
                resource_id=str(job.id),
                ip_address=request.remote_addr,
                user_agent=(request.user_agent.string[:500] if request.user_agent else None),
                after_json={"deleted": True},
            )
        )
        db.session.delete(job)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return error_response("Could not delete schedule.", status=500)

    return success_response({"deleted": True})


@schedules_bp.post("/schedules/<job_id>/toggle")
@login_required
@org_required
def toggle_schedule(job_id: str):
    """Toggle schedule active state and return latest status payload."""

    job = _get_job(job_id)
    if job is None:
        return error_response("Schedule not found.", status=404)

    job.is_active = not bool(job.is_active)
    if job.is_active:
        recalculate_next_run(job)

    action = "schedule.activated" if job.is_active else "schedule.paused"

    try:
        db.session.add(
            AuditLog(
                org_id=g.org.id,
                user_id=current_user.id,
                action=action,
                resource_type="scheduled_job",
                resource_id=str(job.id),
                ip_address=request.remote_addr,
                user_agent=(request.user_agent.string[:500] if request.user_agent else None),
                after_json={"is_active": job.is_active},
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        return error_response("Could not toggle schedule.", status=500)

    return success_response(
        {
            "is_active": job.is_active,
            "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None,
        }
    )
