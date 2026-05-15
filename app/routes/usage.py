"""Usage and quota monitoring blueprint routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, render_template
from sqlalchemy import func

from app.extensions import db
from app.models import AutomationTask, Plan, Subscription, TaskOutput, UsageRecord
from app.services.agent_runner import TASK_REGISTRY
from app.utils.decorators import login_required, org_required

usage_bp = Blueprint("usage", __name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _month_start(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month_start(dt: datetime) -> datetime:
    if dt.month == 12:
        return dt.replace(year=dt.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return dt.replace(month=dt.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)


def _day_bucket(column):
    bind = db.session.get_bind() or db.engine
    if bind.dialect.name == "sqlite":
        return func.strftime("%Y-%m-%d", column)
    return func.date_trunc("day", column)


def _output_size_expr():
    bind = db.session.get_bind() or db.engine
    if bind.dialect.name == "sqlite":
        text_size = func.length(func.coalesce(TaskOutput.content_text, ""))
    else:
        text_size = func.octet_length(func.coalesce(TaskOutput.content_text, ""))
    return func.coalesce(TaskOutput.file_size, text_size, 0)


def _plan_has_api_access(plan: Plan | None) -> bool:
    if plan is None:
        return False
    if plan.slug in {"pro", "team", "enterprise"}:
        return True

    features = plan.features_json or []
    if isinstance(features, list):
        return "api_access" in features or "all_features" in features
    if isinstance(features, dict):
        return bool(features.get("api_access") or features.get("all_features"))
    return False


@usage_bp.get("/usage")
@login_required
@org_required
def usage_dashboard():
    """Render usage and quota monitor page."""

    now = _utcnow()
    current_month_start = _month_start(now)
    next_month_start = _next_month_start(current_month_start)

    plan = db.session.get(Plan, g.org.plan_id) if g.org.plan_id else None
    quota_limit = int(plan.task_quota_monthly) if plan else 10

    tasks_used = (
        UsageRecord.query.filter(
            UsageRecord.org_id == g.org.id,
            UsageRecord.usage_type == "task_run",
            UsageRecord.recorded_at >= current_month_start,
        ).count()
    )

    if quota_limit > 0:
        quota_percent = round(min((tasks_used / quota_limit) * 100, 100), 1)
    else:
        quota_percent = 0.0

    storage_used_bytes = (
        db.session.query(func.coalesce(func.sum(_output_size_expr()), 0))
        .filter(TaskOutput.org_id == g.org.id, TaskOutput.is_deleted.is_(False))
        .scalar()
        or 0
    )
    storage_used_mb = round(storage_used_bytes / (1024 * 1024), 2)

    api_calls_this_month = (
        UsageRecord.query.filter(
            UsageRecord.org_id == g.org.id,
            UsageRecord.usage_type == "api_call",
            UsageRecord.recorded_at >= current_month_start,
        ).count()
    )

    task_type_rows = (
        db.session.query(
            AutomationTask.task_type,
            func.count(UsageRecord.id).label("count"),
        )
        .join(UsageRecord, UsageRecord.task_id == AutomationTask.id)
        .filter(
            UsageRecord.org_id == g.org.id,
            UsageRecord.usage_type == "task_run",
            UsageRecord.recorded_at >= current_month_start,
        )
        .group_by(AutomationTask.task_type)
        .all()
    )

    category_totals = {}
    for row in task_type_rows:
        task_meta = TASK_REGISTRY.get(row.task_type, {})
        category = task_meta.get("category") or "other"
        category_label = task_meta.get("category_display") or str(category).replace("_", " ").title()
        if category not in category_totals:
            category_totals[category] = {"category": category, "label": category_label, "count": 0}
        category_totals[category]["count"] += int(row.count or 0)

    category_breakdown = sorted(category_totals.values(), key=lambda item: item["count"], reverse=True)[:10]
    total_category_usage = sum(item["count"] for item in category_breakdown)
    for item in category_breakdown:
        item["percent"] = round((item["count"] / total_category_usage) * 100, 1) if total_category_usage else 0.0

    thirty_day_start = now - timedelta(days=29)
    day_bucket = _day_bucket(UsageRecord.recorded_at)
    daily_rows = (
        db.session.query(
            day_bucket.label("day"),
            func.count(UsageRecord.id).label("count"),
        )
        .filter(
            UsageRecord.org_id == g.org.id,
            UsageRecord.usage_type == "task_run",
            UsageRecord.recorded_at >= thirty_day_start,
        )
        .group_by(day_bucket)
        .all()
    )

    daily_map = {}
    for row in daily_rows:
        day_value = row.day
        if day_value is None:
            continue
        if hasattr(day_value, "strftime"):
            key = day_value.strftime("%Y-%m-%d")
        else:
            key = str(day_value)[:10]
        daily_map[key] = int(row.count or 0)
    daily_usage = []
    cursor = thirty_day_start.date()
    for _ in range(30):
        key = cursor.strftime("%Y-%m-%d")
        daily_usage.append({"date": key, "count": daily_map.get(key, 0)})
        cursor += timedelta(days=1)

    monthly_history = []
    pointer = current_month_start
    for _ in range(6):
        month_end = _next_month_start(pointer)

        month_tasks_used = (
            UsageRecord.query.filter(
                UsageRecord.org_id == g.org.id,
                UsageRecord.usage_type == "task_run",
                UsageRecord.recorded_at >= pointer,
                UsageRecord.recorded_at < month_end,
            ).count()
        )
        success_count = (
            AutomationTask.query.filter(
                AutomationTask.org_id == g.org.id,
                AutomationTask.status == "done",
                AutomationTask.created_at >= pointer,
                AutomationTask.created_at < month_end,
            ).count()
        )
        failed_count = (
            AutomationTask.query.filter(
                AutomationTask.org_id == g.org.id,
                AutomationTask.status == "failed",
                AutomationTask.created_at >= pointer,
                AutomationTask.created_at < month_end,
            ).count()
        )
        storage_added_bytes = (
            db.session.query(func.coalesce(func.sum(_output_size_expr()), 0))
            .filter(
                TaskOutput.org_id == g.org.id,
                TaskOutput.created_at >= pointer,
                TaskOutput.created_at < month_end,
                TaskOutput.is_deleted.is_(False),
            )
            .scalar()
            or 0
        )

        denominator = success_count + failed_count
        success_rate = round((success_count / denominator) * 100, 1) if denominator else 100.0

        monthly_history.append(
            {
                "month_start": pointer,
                "month_end": month_end - timedelta(days=1),
                "month_label": pointer.strftime("%b %Y"),
                "tasks_used": month_tasks_used,
                "quota": quota_limit,
                "success_rate": success_rate,
                "storage_added_mb": round(storage_added_bytes / (1024 * 1024), 2),
                "plan_name": plan.name if plan else "Free",
            }
        )

        pointer = (pointer.replace(day=1) - timedelta(days=1)).replace(day=1)

    active_subscription = Subscription.query.filter_by(org_id=g.org.id, status="active").first()
    next_plan = None
    if plan is not None:
        next_plan = (
            Plan.query.filter(
                Plan.is_active.is_(True),
                Plan.price_monthly_inr > max(plan.price_monthly_inr, 0),
                Plan.price_monthly_inr > 0,
            )
            .order_by(Plan.price_monthly_inr.asc())
            .first()
        )

    return render_template(
        "app/usage.html",
        plan=plan,
        next_plan=next_plan,
        current_subscription=active_subscription,
        tasks_used=tasks_used,
        quota_limit=quota_limit,
        quota_percent=quota_percent,
        storage_used_mb=storage_used_mb,
        storage_used_bytes=storage_used_bytes,
        api_calls_this_month=api_calls_this_month,
        category_breakdown=category_breakdown,
        daily_usage=daily_usage,
        monthly_history=monthly_history,
        first_of_next_month=next_month_start,
        plan_limits={
            "task_quota": quota_limit,
            "seat_limit": plan.seat_limit if plan else 1,
            "output_retention_days": plan.output_retention_days if plan else 30,
            "api_access": _plan_has_api_access(plan),
        },
    )
