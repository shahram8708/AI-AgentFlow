"""Dashboard and onboarding blueprint routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from flask import Blueprint, flash, g, redirect, render_template, request
from flask_login import current_user

from app.extensions import db
from app.models import (
    AuditLog,
    AutomationTask,
    Plan,
    Subscription,
    TaskOutput,
    UsageRecord,
    Workflow,
    WorkflowTemplate,
)
from app.services.auth_service import AuthService
from app.services.notification_service import NotificationService
from app.utils.decorators import login_required
from app.utils.response_helpers import error_response, success_response

dashboard_bp = Blueprint("dashboard", __name__)

auth_service = AuthService()
notification_service = NotificationService()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _month_start(now_utc: datetime) -> datetime:
    return now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _today_start(now_utc: datetime) -> datetime:
    return now_utc.replace(hour=0, minute=0, second=0, microsecond=0)


def _week_start(now_utc: datetime) -> datetime:
    weekday = now_utc.weekday()
    return _today_start(now_utc) - timedelta(days=weekday)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _get_current_org():
    organization = auth_service.get_user_org(current_user.id)
    if organization is not None:
        g.org = organization
    return organization


def _get_next_plan(current_plan: Plan | None) -> Plan | None:
    if current_plan is None:
        return None
    try:
        base_price = current_plan.price_monthly_inr if current_plan.price_monthly_inr >= 0 else 0
        return (
            Plan.query.filter(
                Plan.is_active.is_(True),
                Plan.price_monthly_inr > base_price,
                Plan.price_monthly_inr > 0,
            )
            .order_by(Plan.price_monthly_inr.asc())
            .first()
        )
    except Exception:
        return None


def collect_dashboard_data(org, user_id: Any) -> dict[str, Any]:
    """Collect dashboard values with query isolation and safe fallbacks."""

    now_utc = _utcnow()
    month_start = _month_start(now_utc)
    today_start = _today_start(now_utc)
    week_start = _week_start(now_utc)

    plan = SimpleNamespace(name="Free", task_quota_monthly=10, price_monthly_inr=0)
    subscription = None

    try:
        if org and org.plan_id:
            plan_record = db.session.get(Plan, org.plan_id)
            if plan_record is not None:
                plan = plan_record
    except Exception:
        pass

    try:
        if org:
            subscription = Subscription.query.filter_by(org_id=org.id, status="active").first()
    except Exception:
        subscription = None

    tasks_this_month = 0
    try:
        if org:
            tasks_this_month = (
                AutomationTask.query.filter(
                    AutomationTask.org_id == org.id,
                    AutomationTask.created_at >= month_start,
                    AutomationTask.status != "cancelled",
                ).count()
            )
    except Exception:
        tasks_this_month = 0

    tasks_completed_today = 0
    try:
        if org:
            tasks_completed_today = (
                AutomationTask.query.filter(
                    AutomationTask.org_id == org.id,
                    AutomationTask.status == "done",
                    AutomationTask.completed_at >= today_start,
                ).count()
            )
    except Exception:
        tasks_completed_today = 0

    tasks_completed_this_week = 0
    try:
        if org:
            tasks_completed_this_week = (
                AutomationTask.query.filter(
                    AutomationTask.org_id == org.id,
                    AutomationTask.status == "done",
                    AutomationTask.completed_at >= week_start,
                ).count()
            )
    except Exception:
        tasks_completed_this_week = 0

    tasks_running = 0
    try:
        if org:
            tasks_running = (
                AutomationTask.query.filter(
                    AutomationTask.org_id == org.id,
                    AutomationTask.status == "running",
                ).count()
            )
    except Exception:
        tasks_running = 0

    tasks_failed_today = 0
    try:
        if org:
            tasks_failed_today = (
                AutomationTask.query.filter(
                    AutomationTask.org_id == org.id,
                    AutomationTask.status == "failed",
                    AutomationTask.created_at >= today_start,
                ).count()
            )
    except Exception:
        tasks_failed_today = 0

    success_rate = 100.0
    try:
        done_month = (
            AutomationTask.query.filter(
                AutomationTask.org_id == org.id,
                AutomationTask.status == "done",
                AutomationTask.created_at >= month_start,
            ).count()
            if org
            else 0
        )
        failed_month = (
            AutomationTask.query.filter(
                AutomationTask.org_id == org.id,
                AutomationTask.status == "failed",
                AutomationTask.created_at >= month_start,
            ).count()
            if org
            else 0
        )
        denominator = done_month + failed_month
        success_rate = round((done_month / denominator) * 100, 1) if denominator > 0 else 100.0
    except Exception:
        success_rate = 100.0

    quota_used = 0
    try:
        if org:
            quota_used = (
                UsageRecord.query.filter(
                    UsageRecord.org_id == org.id,
                    UsageRecord.usage_type == "task_run",
                    UsageRecord.recorded_at >= month_start,
                ).count()
            )
    except Exception:
        quota_used = 0

    quota_limit = int(getattr(plan, "task_quota_monthly", 10) or 10)
    if quota_limit == -1:
        quota_percent = 0.0
    elif quota_limit > 0:
        quota_percent = round(min((quota_used / quota_limit) * 100, 100), 1)
    else:
        quota_percent = 0.0

    recent_tasks = []
    try:
        if org:
            recent_tasks = (
                AutomationTask.query.filter(AutomationTask.org_id == org.id)
                .order_by(AutomationTask.created_at.desc())
                .limit(8)
                .all()
            )
    except Exception:
        recent_tasks = []

    recent_outputs = []
    try:
        if org:
            recent_outputs = (
                TaskOutput.query.join(AutomationTask, TaskOutput.task_id == AutomationTask.id)
                .filter(
                    AutomationTask.org_id == org.id,
                    TaskOutput.is_deleted.is_(False),
                )
                .order_by(TaskOutput.created_at.desc())
                .limit(5)
                .all()
            )
            for output in recent_outputs:
                if output.task is not None and not getattr(output, "task_name", None):
                    output.task_name = output.task.task_name
    except Exception:
        recent_outputs = []

    saved_workflows = []
    try:
        if org:
            saved_workflows = (
                Workflow.query.filter(
                    Workflow.org_id == org.id,
                    Workflow.is_deleted.is_(False),
                )
                .order_by(Workflow.last_run_at.desc().nullslast(), Workflow.created_at.desc())
                .limit(4)
                .all()
            )
    except Exception:
        saved_workflows = []

    featured_templates = []
    try:
        featured_templates = (
            WorkflowTemplate.query.filter_by(is_featured=True, is_active=True)
            .order_by(WorkflowTemplate.created_at.desc())
            .limit(3)
            .all()
        )
    except Exception:
        featured_templates = []

    activity_feed = []
    try:
        if org:
            activity_feed = (
                AuditLog.query.filter(AuditLog.org_id == org.id)
                .order_by(AuditLog.timestamp.desc())
                .limit(10)
                .all()
            )
    except Exception:
        activity_feed = []

    unread_count = 0
    try:
        unread_count = notification_service.get_unread_count(user_id, org.id if org else None)
    except Exception:
        unread_count = 0

    next_plan = _get_next_plan(plan if isinstance(plan, Plan) else None)

    return {
        "org": org,
        "plan": plan,
        "subscription": subscription,
        "tasks_this_month": tasks_this_month,
        "tasks_completed_today": tasks_completed_today,
        "tasks_completed_this_week": tasks_completed_this_week,
        "tasks_running": tasks_running,
        "tasks_failed_today": tasks_failed_today,
        "success_rate": success_rate,
        "quota_used": quota_used,
        "quota_limit": quota_limit,
        "quota_percent": quota_percent,
        "recent_tasks": recent_tasks,
        "recent_outputs": recent_outputs,
        "saved_workflows": saved_workflows,
        "featured_templates": featured_templates,
        "activity_feed": activity_feed,
        "unread_count": unread_count,
        "show_onboarding": not bool(getattr(current_user, "onboarding_completed", False)),
        "next_plan": next_plan,
    }


@dashboard_bp.get("/dashboard")
@login_required
def dashboard_home():
    """Render dashboard view for authenticated users."""

    org = _get_current_org()
    if org is None:
        flash("Please complete your organization setup.", "warning")
        return redirect("/settings/account")

    context = collect_dashboard_data(org, current_user.id)
    return render_template("app/dashboard.html", **context)


@dashboard_bp.get("/onboarding")
@login_required
def onboarding():
    """Render onboarding wizard page."""

    if current_user.onboarding_completed:
        flash("You've already completed onboarding.", "info")
        return redirect("/dashboard")

    featured_templates = []
    try:
        featured_templates = (
            WorkflowTemplate.query.filter_by(is_featured=True, is_active=True)
            .order_by(WorkflowTemplate.created_at.desc())
            .limit(6)
            .all()
        )
    except Exception:
        featured_templates = []

    try:
        server_step = int(request.args.get("step", 1))
    except (TypeError, ValueError):
        server_step = 1

    server_step = max(1, min(server_step, 4))

    return render_template(
        "app/onboarding.html",
        featured_templates=featured_templates,
        server_step=server_step,
        saved_preferences=current_user.preferences_json or {},
    )


@dashboard_bp.post("/onboarding")
@login_required
def onboarding_post():
    """Persist onboarding progress from JSON or form submissions."""

    payload: dict[str, Any]
    is_json = request.is_json
    if is_json:
        payload = request.get_json(silent=True) or {}
    else:
        payload = request.form.to_dict(flat=True)

    try:
        step = int(payload.get("step", 1))
    except (TypeError, ValueError):
        step = 1
    step = max(1, min(step, 4))

    persona = payload.get("persona")
    team_size = payload.get("team_size")
    use_case = payload.get("use_case")
    completed = _as_bool(payload.get("completed", False))

    org = _get_current_org()
    if org is None:
        if is_json:
            return error_response("Organization not found.", status=403)
        flash("Please complete your organization setup.", "warning")
        return redirect("/settings/account")

    try:
        preferences = current_user.preferences_json if isinstance(current_user.preferences_json, dict) else {}
        if persona:
            preferences["persona"] = persona
        if team_size:
            preferences["team_size"] = team_size
        if use_case:
            preferences["use_case"] = use_case
        preferences["onboarding_step"] = step
        current_user.preferences_json = preferences

        if completed:
            current_user.onboarding_completed = True
            audit_entry = AuditLog(
                org_id=org.id,
                user_id=current_user.id,
                action="user.onboarding_completed",
                resource_type="user",
                resource_id=str(current_user.id),
                extra_json={"step": step, "persona": persona, "team_size": team_size},
            )
            db.session.add(audit_entry)

        db.session.commit()
    except Exception:
        db.session.rollback()
        if is_json:
            return error_response("Could not save onboarding progress.", status=500)
        flash("Could not save onboarding progress. Please try again.", "danger")
        return redirect("/onboarding")

    if completed:
        if is_json:
            return success_response({"redirect": "/dashboard"})
        flash("Onboarding completed successfully.", "success")
        return redirect("/dashboard")

    if is_json:
        return success_response({"step": step})

    next_step = max(1, min(step + 1, 4))
    return redirect(f"/onboarding?step={next_step}")
