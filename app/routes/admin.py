"""Platform admin blueprint routes."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from time import perf_counter
from uuid import UUID

import psutil
from flask import Blueprint, abort, current_app, g, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user
from sqlalchemy import and_, func, or_, text

from app.extensions import db
from app.models import (
    AuditLog,
    AutomationTask,
    DataSource,
    FeatureFlag,
    Invoice,
    KnowledgeBaseEntry,
    Organization,
    OrganizationMember,
    Plan,
    Project,
    Subscription,
    SupportTicket,
    TaskOutput,
    User,
    Workflow,
)
from app.services.auth_service import AuthService
from app.services.email_service import EmailService
from app.tasks import celery as celery_app
from app.utils.decorators import admin_required, login_required
from app.utils.response_helpers import error_response, success_response

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

auth_service = AuthService()
email_service = EmailService()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _start_of_today(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _start_of_week(now: datetime) -> datetime:
    return _start_of_today(now) - timedelta(days=now.weekday())


def _start_of_month(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _month_bucket(column):
    bind = db.session.get_bind() or db.engine
    if bind.dialect.name == "sqlite":
        return func.strftime("%Y-%m-01", column)
    return func.date_trunc("month", column)


def _coerce_uuid(value: str):
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _monthly_equivalent_paise(subscription: Subscription, plan: Plan | None) -> int:
    if plan is None:
        return 0
    if subscription.billing_cycle == "annual":
        if plan.price_annual_inr < 0:
            return 0
        return int(plan.price_annual_inr / 12)
    if plan.price_monthly_inr < 0:
        return 0
    return int(plan.price_monthly_inr)


def _write_admin_audit(action: str, resource_type: str | None = None, resource_id: str | None = None, org_id=None, extra_json=None) -> None:
    db.session.add(
        AuditLog(
            org_id=org_id,
            user_id=current_user.id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=request.remote_addr,
            user_agent=(request.user_agent.string or "")[:500],
            extra_json=extra_json,
        )
    )


def _human_uptime(seconds: int) -> str:
    if seconds <= 0:
        return "0m"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _parse_pool_status(pool_status_text: str) -> dict:
    parsed = {
        "raw": pool_status_text,
        "size": 0,
        "checkedin": 0,
        "overflow": 0,
        "checkedout": 0,
    }
    match = re.search(
        r"Pool size: (\d+)\s+Connections in pool: (\d+)\s+Current Overflow: (-?\d+)\s+Current Checked out connections: (\d+)",
        pool_status_text,
    )
    if match:
        parsed["size"] = _safe_int(match.group(1))
        parsed["checkedin"] = _safe_int(match.group(2))
        parsed["overflow"] = _safe_int(match.group(3))
        parsed["checkedout"] = _safe_int(match.group(4))
    return parsed


def collect_system_metrics() -> dict:
    """Collect database, queue, worker, and host metrics for admin system page."""

    metrics = {
        "database": {
            "status": "operational",
            "response_ms": None,
            "version": "Unknown",
            "pool": {"raw": "", "size": 0, "checkedin": 0, "overflow": 0, "checkedout": 0},
            "tables": {},
        },
        "redis": {
            "status": "operational",
            "response_ms": None,
            "memory_used": "Unknown",
            "connected_clients": 0,
            "queue_default": 0,
            "queue_high": 0,
            "queue_low": 0,
        },
        "celery": {
            "status": "operational",
            "worker_count": 0,
            "active_task_count": 0,
            "workers": [],
            "queue_default": 0,
            "queue_high": 0,
            "queue_low": 0,
            "warning": "",
        },
        "system": {
            "cpu_percent": 0.0,
            "memory_percent": 0.0,
            "memory_used_gb": 0.0,
            "memory_total_gb": 0.0,
            "disk_percent": 0.0,
            "disk_used_gb": 0.0,
            "disk_free_gb": 0.0,
            "disk_total_gb": 0.0,
            "load_average": [0.0, 0.0, 0.0],
            "cpu_count": psutil.cpu_count() or 0,
        },
        "application": {
            "error_rate_24h": 0.0,
            "recent_errors": [],
            "uptime_seconds": 0,
            "uptime_human": "0m",
        },
        "last_refreshed": datetime.now(timezone.utc).isoformat(),
    }

    db_start = perf_counter()
    try:
        db.session.execute(text("SELECT 1"))
        metrics["database"]["response_ms"] = round((perf_counter() - db_start) * 1000, 2)
        version = db.session.execute(text("SELECT version()")).scalar()
        metrics["database"]["version"] = str(version or "Unknown")
        metrics["database"]["pool"] = _parse_pool_status(db.engine.pool.status())

        for table_name in ["automation_tasks", "users", "organizations", "audit_logs"]:
            count = db.session.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
            metrics["database"]["tables"][table_name] = int(count or 0)
    except Exception as exc:  # pylint: disable=broad-except
        metrics["database"]["status"] = "outage"
        metrics["database"]["error"] = str(exc)

    redis_client = current_app.extensions.get("redis_client")
    redis_start = perf_counter()
    if redis_client is not None:
        try:
            redis_client.ping()
            metrics["redis"]["response_ms"] = round((perf_counter() - redis_start) * 1000, 2)
            memory_info = redis_client.info("memory")
            clients_info = redis_client.info("clients")
            queue_default = _safe_int(redis_client.llen("default"))
            queue_high = _safe_int(redis_client.llen("high"))
            queue_low = _safe_int(redis_client.llen("low"))

            metrics["redis"]["memory_used"] = memory_info.get("used_memory_human", "Unknown")
            metrics["redis"]["connected_clients"] = _safe_int(clients_info.get("connected_clients", 0))
            metrics["redis"]["queue_default"] = queue_default
            metrics["redis"]["queue_high"] = queue_high
            metrics["redis"]["queue_low"] = queue_low

            metrics["celery"]["queue_default"] = queue_default
            metrics["celery"]["queue_high"] = queue_high
            metrics["celery"]["queue_low"] = queue_low
        except Exception as exc:  # pylint: disable=broad-except
            metrics["redis"]["status"] = "degraded"
            metrics["redis"]["error"] = str(exc)
    else:
        metrics["redis"]["status"] = "outage"

    try:
        inspect = celery_app.control.inspect(timeout=2.0)
        active_by_worker = inspect.active() if inspect else None
        stats_by_worker = inspect.stats() if inspect else None

        if not active_by_worker:
            metrics["celery"]["status"] = "degraded"
            metrics["celery"]["warning"] = "No workers connected"
        else:
            workers = []
            active_task_count = 0
            for hostname, tasks in active_by_worker.items():
                current_tasks = len(tasks or [])
                active_task_count += current_tasks
                processed = 0
                if stats_by_worker and hostname in stats_by_worker:
                    processed = _safe_int(stats_by_worker[hostname].get("total", {}).get("app.tasks.agent_tasks.run_agent_task", 0))
                workers.append(
                    {
                        "hostname": hostname,
                        "tasks_processed": processed,
                        "current_tasks": current_tasks,
                    }
                )

            metrics["celery"]["workers"] = workers
            metrics["celery"]["worker_count"] = len(workers)
            metrics["celery"]["active_task_count"] = active_task_count
    except Exception as exc:  # pylint: disable=broad-except
        metrics["celery"]["status"] = "degraded"
        metrics["celery"]["warning"] = str(exc)

    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(os.path.abspath(os.sep))
    metrics["system"]["cpu_percent"] = round(psutil.cpu_percent(interval=0.1), 1)
    metrics["system"]["memory_percent"] = round(memory.percent, 1)
    metrics["system"]["memory_used_gb"] = round(memory.used / (1024**3), 2)
    metrics["system"]["memory_total_gb"] = round(memory.total / (1024**3), 2)
    metrics["system"]["disk_percent"] = round(disk.percent, 1)
    metrics["system"]["disk_used_gb"] = round(disk.used / (1024**3), 2)
    metrics["system"]["disk_free_gb"] = round(disk.free / (1024**3), 2)
    metrics["system"]["disk_total_gb"] = round(disk.total / (1024**3), 2)
    try:
        metrics["system"]["load_average"] = [round(v, 2) for v in psutil.getloadavg()]
    except (AttributeError, OSError):
        metrics["system"]["load_average"] = [0.0, 0.0, 0.0]

    twenty_four_hours_ago = _utcnow() - timedelta(hours=24)
    failed_24h = (
        AutomationTask.query.filter(
            AutomationTask.status == "failed",
            AutomationTask.created_at >= twenty_four_hours_ago,
        ).count()
    )
    total_24h = AutomationTask.query.filter(AutomationTask.created_at >= twenty_four_hours_ago).count()
    metrics["application"]["error_rate_24h"] = round((failed_24h / total_24h) * 100, 2) if total_24h else 0.0

    recent_error_logs = (
        AuditLog.query.filter(AuditLog.action.like("%.error%"))
        .order_by(AuditLog.timestamp.desc())
        .limit(20)
        .all()
    )
    metrics["application"]["recent_errors"] = [
        {
            "timestamp": entry.timestamp.isoformat() if entry.timestamp else "",
            "action": entry.action,
            "org_id": str(entry.org_id) if entry.org_id else "",
            "resource_id": entry.resource_id or "",
            "summary": (entry.extra_json or {}).get("message") if isinstance(entry.extra_json, dict) else "",
        }
        for entry in recent_error_logs
    ]

    start_time_iso = None
    if redis_client is not None:
        try:
            start_value = redis_client.get("app:start_time")
            if isinstance(start_value, bytes):
                start_value = start_value.decode("utf-8")
            start_time_iso = start_value
        except Exception:  # pylint: disable=broad-except
            start_time_iso = None

    if start_time_iso:
        try:
            start_time = datetime.fromisoformat(start_time_iso)
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            uptime_seconds = max(int((_utcnow() - start_time).total_seconds()), 0)
        except ValueError:
            uptime_seconds = 0
    else:
        uptime_seconds = 0

    metrics["application"]["uptime_seconds"] = uptime_seconds
    metrics["application"]["uptime_human"] = _human_uptime(uptime_seconds)

    return metrics


@admin_bp.before_request
@login_required
def require_admin():
    """Enforce platform admin access for all admin routes."""

    if request.endpoint == "admin.return_impersonation" and session.get("admin_impersonating_user_id"):
        return None
    if not current_user.is_admin:
        abort(403)
    return None


@admin_bp.get("/")
def admin_dashboard():
    """Render platform-wide admin dashboard."""

    now = _utcnow()
    today_start = _start_of_today(now)
    week_start = _start_of_week(now)
    month_start = _start_of_month(now)

    total_users = User.query.filter_by(is_active=True).count()
    new_users_today = User.query.filter(User.created_at >= today_start).count()
    new_users_this_week = User.query.filter(User.created_at >= week_start).count()
    new_users_this_month = User.query.filter(User.created_at >= month_start).count()
    dau = (
        db.session.query(func.count(func.distinct(AuditLog.user_id)))
        .filter(AuditLog.action == "user.login", AuditLog.timestamp >= today_start)
        .scalar()
        or 0
    )
    mau = (
        db.session.query(func.count(func.distinct(AuditLog.user_id)))
        .filter(AuditLog.action == "user.login", AuditLog.timestamp >= now - timedelta(days=30))
        .scalar()
        or 0
    )

    total_orgs = Organization.query.filter_by(is_deleted=False).count()
    total_tasks_all_time = AutomationTask.query.count()
    tasks_today = AutomationTask.query.filter(AutomationTask.created_at >= today_start).count()
    tasks_this_month = AutomationTask.query.filter(AutomationTask.created_at >= month_start).count()
    running_tasks = AutomationTask.query.filter(AutomationTask.status == "running").count()

    active_subscriptions_rows = (
        Subscription.query.filter(Subscription.status == "active").join(Plan, Subscription.plan_id == Plan.id).all()
    )
    mrr_paise = sum(_monthly_equivalent_paise(sub, sub.plan) for sub in active_subscriptions_rows)
    active_subscriptions = len(active_subscriptions_rows)

    failed_24h = AutomationTask.query.filter(
        AutomationTask.status == "failed", AutomationTask.created_at >= now - timedelta(hours=24)
    ).count()
    total_24h = AutomationTask.query.filter(AutomationTask.created_at >= now - timedelta(hours=24)).count()
    error_rate_24h = round((failed_24h / total_24h) * 100, 2) if total_24h else 0.0

    done_30d = AutomationTask.query.filter(
        AutomationTask.status == "done", AutomationTask.created_at >= now - timedelta(days=30)
    ).count()
    failed_30d = AutomationTask.query.filter(
        AutomationTask.status == "failed", AutomationTask.created_at >= now - timedelta(days=30)
    ).count()
    avg_task_success_rate = round((done_30d / (done_30d + failed_30d)) * 100, 2) if (done_30d + failed_30d) else 100.0

    plan_distribution_rows = (
        db.session.query(Plan.name, func.count(Organization.id))
        .join(Organization, Organization.plan_id == Plan.id)
        .filter(Organization.is_deleted.is_(False))
        .group_by(Plan.name)
        .all()
    )
    plan_distribution = [{"plan": name, "count": int(count)} for name, count in plan_distribution_rows]

    recent_signups = User.query.order_by(User.created_at.desc()).limit(10).all()
    signup_context = []
    for user in recent_signups:
        membership = (
            OrganizationMember.query.filter_by(user_id=user.id)
            .order_by(OrganizationMember.created_at.desc())
            .first()
        )
        organization = membership.organization if membership else None
        signup_context.append(
            {
                "user": user,
                "organization": organization,
                "plan": organization.plan if organization else None,
            }
        )

    recent_tasks = (
        AutomationTask.query.join(Organization, Organization.id == AutomationTask.org_id)
        .order_by(AutomationTask.created_at.desc())
        .limit(10)
        .all()
    )
    admin_count = User.query.filter(User.role == "admin").count()

    return render_template(
        "admin/admin_dashboard.html",
        total_users=total_users,
        new_users_today=new_users_today,
        new_users_this_week=new_users_this_week,
        new_users_this_month=new_users_this_month,
        dau=dau,
        mau=mau,
        total_orgs=total_orgs,
        total_tasks_all_time=total_tasks_all_time,
        tasks_today=tasks_today,
        tasks_this_month=tasks_this_month,
        running_tasks=running_tasks,
        mrr_paise=mrr_paise,
        error_rate_24h=error_rate_24h,
        active_subscriptions=active_subscriptions,
        plan_distribution=plan_distribution,
        recent_signups=signup_context,
        recent_tasks=recent_tasks,
        admin_count=admin_count,
        avg_task_success_rate=avg_task_success_rate,
    )


@admin_bp.get("/users")
def admin_users():
    """Render platform user management page with filters."""

    search = (request.args.get("search") or "").strip()
    plan_filter = (request.args.get("plan") or "").strip().lower()
    role_filter = (request.args.get("role") or "").strip().lower()
    status_filter = (request.args.get("status") or "").strip().lower()
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()

    query = User.query

    if search:
        query = query.filter(
            or_(
                User.first_name.ilike(f"%{search}%"),
                User.last_name.ilike(f"%{search}%"),
                User.email.ilike(f"%{search}%"),
            )
        )

    if role_filter in {"user", "admin"}:
        query = query.filter(User.role == role_filter)

    if status_filter == "active":
        query = query.filter(User.is_active.is_(True))
    elif status_filter == "banned":
        query = query.filter(User.is_active.is_(False))
    elif status_filter == "unverified":
        query = query.filter(User.is_verified.is_(False))

    if date_from:
        try:
            start = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            query = query.filter(User.created_at >= start)
        except ValueError:
            pass
    if date_to:
        try:
            end = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
            query = query.filter(User.created_at < end)
        except ValueError:
            pass

    if plan_filter:
        query = (
            query.join(OrganizationMember, OrganizationMember.user_id == User.id)
            .join(Organization, Organization.id == OrganizationMember.org_id)
            .join(Plan, Plan.id == Organization.plan_id)
            .filter(or_(Plan.slug == plan_filter, func.lower(Plan.name) == plan_filter))
        )

    query = query.distinct(User.id)
    page = request.args.get("page", default=1, type=int)
    pagination = query.order_by(User.created_at.desc()).paginate(page=page, per_page=25, error_out=False)

    user_ids = [user.id for user in pagination.items]
    memberships = (
        OrganizationMember.query.join(Organization, Organization.id == OrganizationMember.org_id)
        .filter(OrganizationMember.user_id.in_(user_ids), Organization.is_deleted.is_(False))
        .all()
    )
    membership_map = {member.user_id: member for member in memberships}

    stats = {
        "total_users": User.query.count(),
        "verified_percent": round((User.query.filter(User.is_verified.is_(True)).count() / max(User.query.count(), 1)) * 100, 1),
        "admin_count": User.query.filter(User.role == "admin").count(),
        "banned_count": User.query.filter(User.is_active.is_(False)).count(),
    }

    plans = Plan.query.filter(Plan.is_active.is_(True)).order_by(Plan.price_monthly_inr.asc()).all()

    return render_template(
        "admin/admin_users.html",
        pagination=pagination,
        users=pagination.items,
        membership_map=membership_map,
        plans=plans,
        stats=stats,
        search=search,
        selected_plan=plan_filter,
        selected_role=role_filter,
        selected_status=status_filter,
        date_from=date_from,
        date_to=date_to,
    )


@admin_bp.post("/users/<user_id>/ban")
def ban_user(user_id: str):
    target_id = _coerce_uuid(user_id)
    if target_id is None:
        return error_response("Invalid user ID.", 400)

    user = db.session.get(User, target_id)
    if user is None:
        return error_response("User not found.", 404)

    user.is_active = False
    _write_admin_audit("admin.user_banned", "user", str(user.id))
    db.session.commit()
    return success_response({"user_id": str(user.id), "is_active": user.is_active})


@admin_bp.post("/users/<user_id>/unban")
def unban_user(user_id: str):
    target_id = _coerce_uuid(user_id)
    if target_id is None:
        return error_response("Invalid user ID.", 400)

    user = db.session.get(User, target_id)
    if user is None:
        return error_response("User not found.", 404)

    user.is_active = True
    _write_admin_audit("admin.user_unbanned", "user", str(user.id))
    db.session.commit()
    return success_response({"user_id": str(user.id), "is_active": user.is_active})


@admin_bp.post("/users/<user_id>/verify")
def force_verify_user(user_id: str):
    target_id = _coerce_uuid(user_id)
    if target_id is None:
        return error_response("Invalid user ID.", 400)

    user = db.session.get(User, target_id)
    if user is None:
        return error_response("User not found.", 404)

    user.is_verified = True
    _write_admin_audit("admin.user_force_verified", "user", str(user.id))
    db.session.commit()
    return success_response({"user_id": str(user.id), "is_verified": user.is_verified})


@admin_bp.post("/users/<user_id>/impersonate")
def impersonate_user(user_id: str):
    target_id = _coerce_uuid(user_id)
    if target_id is None:
        return error_response("Invalid user ID.", 400)

    user = db.session.get(User, target_id)
    if user is None:
        return error_response("User not found.", 404)
    if not user.is_active:
        return error_response("Cannot impersonate an inactive user.", 400)

    session["admin_impersonating_user_id"] = str(current_user.id)
    login_user(user)
    _write_admin_audit(
        "admin.user_impersonated",
        "user",
        str(user.id),
        extra_json={"impersonated_email": user.email},
    )
    db.session.commit()
    return success_response({"redirect": "/dashboard", "impersonating": str(user.id)})


@admin_bp.post("/return-impersonation")
@login_required
def return_impersonation():
    """Return from impersonated session back to original admin account."""

    admin_id_raw = session.pop("admin_impersonating_user_id", None)
    admin_id = _coerce_uuid(admin_id_raw)
    if admin_id is None:
        return redirect(url_for("dashboard.dashboard_home"))

    admin_user = db.session.get(User, admin_id)
    if admin_user is None or not admin_user.is_admin:
        return redirect(url_for("auth.login"))

    login_user(admin_user)
    return redirect(url_for("admin.admin_dashboard"))


@admin_bp.post("/users/<user_id>/reset-password-link")
def admin_send_reset_link(user_id: str):
    target_id = _coerce_uuid(user_id)
    if target_id is None:
        return error_response("Invalid user ID.", 400)

    user = db.session.get(User, target_id)
    if user is None:
        return error_response("User not found.", 404)

    token_payload = auth_service.generate_password_reset_token(user.email)
    if token_payload is None:
        return error_response("Unable to generate password reset token.", 500)

    reset_user, token = token_payload
    email_service.send_password_reset_email(reset_user, token, request.host_url)
    _write_admin_audit("admin.user_password_reset_link_sent", "user", str(user.id))
    db.session.commit()
    return success_response({"user_id": str(user.id), "email": user.email})


@admin_bp.get("/orgs")
def admin_orgs():
    """Render organization management view for admins."""

    search = (request.args.get("search") or "").strip()
    plan_filter = (request.args.get("plan") or "").strip().lower()
    status_filter = (request.args.get("status") or "").strip().lower()

    query = Organization.query.filter(Organization.is_deleted.is_(False))

    if search:
        query = query.filter(Organization.name.ilike(f"%{search}%"))

    if plan_filter:
        query = query.join(Plan, Organization.plan_id == Plan.id).filter(
            or_(func.lower(Plan.slug) == plan_filter, func.lower(Plan.name) == plan_filter)
        )

    if status_filter == "suspended":
        query = query.filter(text("COALESCE((settings_json->>'suspended')::boolean, false) = true"))
    elif status_filter == "active":
        query = query.filter(text("COALESCE((settings_json->>'suspended')::boolean, false) = false"))

    page = request.args.get("page", default=1, type=int)
    pagination = query.order_by(Organization.created_at.desc()).paginate(page=page, per_page=25, error_out=False)

    org_ids = [org.id for org in pagination.items]

    task_counts = dict(
        db.session.query(AutomationTask.org_id, func.count(AutomationTask.id))
        .filter(AutomationTask.org_id.in_(org_ids))
        .group_by(AutomationTask.org_id)
        .all()
    )

    subscriptions = (
        Subscription.query.filter(Subscription.org_id.in_(org_ids), Subscription.status == "active")
        .join(Plan, Subscription.plan_id == Plan.id)
        .all()
    )
    subscription_map = {sub.org_id: sub for sub in subscriptions}

    plans = Plan.query.filter(Plan.is_active.is_(True)).order_by(Plan.price_monthly_inr.asc()).all()

    stats = {
        "total_orgs": Organization.query.filter(Organization.is_deleted.is_(False)).count(),
        "paying_orgs": Organization.query.join(Plan, Organization.plan_id == Plan.id).filter(Organization.is_deleted.is_(False), Plan.slug != "free").count(),
        "free_orgs": Organization.query.join(Plan, Organization.plan_id == Plan.id).filter(Organization.is_deleted.is_(False), Plan.slug == "free").count(),
        "suspended_orgs": Organization.query.filter(Organization.is_deleted.is_(False)).filter(text("COALESCE((settings_json->>'suspended')::boolean, false) = true")).count(),
    }

    return render_template(
        "admin/admin_orgs.html",
        pagination=pagination,
        organizations=pagination.items,
        task_counts=task_counts,
        subscription_map=subscription_map,
        plans=plans,
        stats=stats,
        selected_plan=plan_filter,
        selected_status=status_filter,
        search=search,
    )


@admin_bp.post("/orgs/<org_id>/change-plan")
def change_org_plan(org_id: str):
    target_id = _coerce_uuid(org_id)
    if target_id is None:
        return error_response("Invalid organization ID.", 400)

    org = db.session.get(Organization, target_id)
    if org is None or org.is_deleted:
        return error_response("Organization not found.", 404)

    payload = request.get_json(silent=True) or request.form
    plan_id = _coerce_uuid((payload.get("plan_id") if isinstance(payload, dict) else payload.get("plan_id")) or "")
    if plan_id is None:
        return error_response("Invalid plan ID.", 400)

    plan = db.session.get(Plan, plan_id)
    if plan is None:
        return error_response("Plan not found.", 404)

    org.plan_id = plan.id
    active_subscription = Subscription.query.filter_by(org_id=org.id, status="active").first()
    if active_subscription:
        active_subscription.plan_id = plan.id

    _write_admin_audit(
        "admin.org_plan_changed",
        "organization",
        str(org.id),
        org_id=org.id,
        extra_json={"new_plan": plan.slug},
    )
    db.session.commit()
    return success_response({"org_id": str(org.id), "plan": plan.name})


@admin_bp.post("/orgs/<org_id>/suspend")
def suspend_org(org_id: str):
    target_id = _coerce_uuid(org_id)
    if target_id is None:
        return error_response("Invalid organization ID.", 400)

    org = db.session.get(Organization, target_id)
    if org is None or org.is_deleted:
        return error_response("Organization not found.", 404)

    settings = dict(org.settings_json or {})
    settings["suspended"] = True
    settings["suspended_at"] = _utcnow().isoformat()
    settings["suspended_by"] = str(current_user.id)
    org.settings_json = settings

    _write_admin_audit("admin.org_suspended", "organization", str(org.id), org_id=org.id)
    db.session.commit()
    return success_response({"org_id": str(org.id), "suspended": True})


@admin_bp.post("/orgs/<org_id>/unsuspend")
def unsuspend_org(org_id: str):
    target_id = _coerce_uuid(org_id)
    if target_id is None:
        return error_response("Invalid organization ID.", 400)

    org = db.session.get(Organization, target_id)
    if org is None or org.is_deleted:
        return error_response("Organization not found.", 404)

    settings = dict(org.settings_json or {})
    settings["suspended"] = False
    settings["unsuspended_at"] = _utcnow().isoformat()
    settings["unsuspended_by"] = str(current_user.id)
    org.settings_json = settings

    _write_admin_audit("admin.org_unsuspended", "organization", str(org.id), org_id=org.id)
    db.session.commit()
    return success_response({"org_id": str(org.id), "suspended": False})


@admin_bp.delete("/orgs/<org_id>")
def delete_org(org_id: str):
    """Soft-delete organization and cascade soft-delete markers."""

    target_id = _coerce_uuid(org_id)
    if target_id is None:
        return error_response("Invalid organization ID.", 400)

    org = db.session.get(Organization, target_id)
    if org is None or org.is_deleted:
        return error_response("Organization not found.", 404)

    now = _utcnow()
    org.is_deleted = True
    org.deleted_at = now
    org.slug = f"deleted-{str(org.id)[:8]}"

    Project.query.filter_by(org_id=org.id, is_deleted=False).update(
        {Project.is_deleted: True, Project.deleted_at: now}, synchronize_session=False
    )
    Workflow.query.filter_by(org_id=org.id, is_deleted=False).update(
        {Workflow.is_deleted: True, Workflow.deleted_at: now}, synchronize_session=False
    )
    TaskOutput.query.filter_by(org_id=org.id, is_deleted=False).update(
        {TaskOutput.is_deleted: True, TaskOutput.deleted_at: now}, synchronize_session=False
    )
    KnowledgeBaseEntry.query.filter_by(org_id=org.id, is_deleted=False).update(
        {KnowledgeBaseEntry.is_deleted: True, KnowledgeBaseEntry.deleted_at: now}, synchronize_session=False
    )
    SupportTicket.query.filter_by(org_id=org.id, is_deleted=False).update(
        {SupportTicket.is_deleted: True, SupportTicket.deleted_at: now}, synchronize_session=False
    )
    DataSource.query.filter_by(org_id=org.id, is_deleted=False).update(
        {DataSource.is_deleted: True, DataSource.deleted_at: now}, synchronize_session=False
    )
    AutomationTask.query.filter(
        AutomationTask.org_id == org.id,
        AutomationTask.status.in_(["pending", "running"]),
    ).update(
        {
            AutomationTask.status: "cancelled",
            AutomationTask.error_message: "Organization deleted by platform admin",
            AutomationTask.completed_at: now,
        },
        synchronize_session=False,
    )
    Subscription.query.filter_by(org_id=org.id, status="active").update(
        {Subscription.status: "cancelled", Subscription.cancelled_at: now},
        synchronize_session=False,
    )

    _write_admin_audit("admin.org_deleted", "organization", str(org.id), org_id=org.id)
    db.session.commit()
    return success_response({"org_id": str(org.id), "deleted": True})


@admin_bp.get("/flags")
def admin_flags():
    """Render feature flag management page."""

    flags = FeatureFlag.query.order_by(FeatureFlag.created_at.desc()).all()
    org_map = {str(org.id): org for org in Organization.query.filter(Organization.is_deleted.is_(False)).all()}

    return render_template("admin/admin_flags.html", flags=flags, org_map=org_map)


@admin_bp.post("/flags/<flag_key>/toggle")
def toggle_flag(flag_key: str):
    flag = FeatureFlag.query.filter_by(flag_key=flag_key).first()
    if flag is None:
        return error_response("Feature flag not found.", 404)

    flag.is_enabled = not bool(flag.is_enabled)
    _write_admin_audit(
        "admin.flag_toggled",
        "feature_flag",
        str(flag.id),
        extra_json={"flag_key": flag.flag_key, "is_enabled": flag.is_enabled},
    )
    db.session.commit()
    return success_response({"flag_key": flag.flag_key, "is_enabled": flag.is_enabled})


@admin_bp.post("/flags/<flag_key>/rollout")
def rollout_flag(flag_key: str):
    flag = FeatureFlag.query.filter_by(flag_key=flag_key).first()
    if flag is None:
        return error_response("Feature flag not found.", 404)

    payload = request.get_json(silent=True) or request.form
    raw_value = payload.get("rollout_percentage") if isinstance(payload, dict) else payload.get("rollout_percentage")
    rollout = _safe_int(raw_value, -1)
    if rollout < 0 or rollout > 100:
        return error_response("rollout_percentage must be between 0 and 100.", 400)

    flag.rollout_percentage = rollout
    _write_admin_audit(
        "admin.flag_rollout_updated",
        "feature_flag",
        str(flag.id),
        extra_json={"flag_key": flag.flag_key, "rollout_percentage": rollout},
    )
    db.session.commit()
    return success_response({"flag_key": flag.flag_key, "rollout_percentage": flag.rollout_percentage})


@admin_bp.post("/flags/<flag_key>/enable-org")
def enable_org_for_flag(flag_key: str):
    flag = FeatureFlag.query.filter_by(flag_key=flag_key).first()
    if flag is None:
        return error_response("Feature flag not found.", 404)

    payload = request.get_json(silent=True) or request.form
    org_id_raw = payload.get("org_id") if isinstance(payload, dict) else payload.get("org_id")
    org_uuid = _coerce_uuid(org_id_raw)
    if org_uuid is None:
        return error_response("Invalid organization ID.", 400)

    org = db.session.get(Organization, org_uuid)
    if org is None or org.is_deleted:
        return error_response("Organization not found.", 404)

    enabled_ids = list(flag.enabled_org_ids or [])
    if str(org.id) not in enabled_ids:
        enabled_ids.append(str(org.id))
    flag.enabled_org_ids = enabled_ids

    _write_admin_audit(
        "admin.flag_org_enabled",
        "feature_flag",
        str(flag.id),
        extra_json={"flag_key": flag.flag_key, "org_id": str(org.id)},
    )
    db.session.commit()
    return success_response({"flag_key": flag.flag_key, "enabled_org_ids": flag.enabled_org_ids})


@admin_bp.post("/flags")
def create_flag():
    payload = request.get_json(silent=True) or request.form
    flag_key = ((payload.get("flag_key") if isinstance(payload, dict) else payload.get("flag_key")) or "").strip()
    display_name = ((payload.get("display_name") if isinstance(payload, dict) else payload.get("display_name")) or "").strip()
    description = ((payload.get("description") if isinstance(payload, dict) else payload.get("description")) or "").strip()
    is_enabled = str((payload.get("is_enabled") if isinstance(payload, dict) else payload.get("is_enabled")) or "false").lower() in {"true", "1", "yes", "on"}
    rollout_percentage = _safe_int(
        (payload.get("rollout_percentage") if isinstance(payload, dict) else payload.get("rollout_percentage")),
        0,
    )

    if not flag_key or not re.match(r"^[a-z0-9_]{3,100}$", flag_key):
        return error_response("flag_key must be snake_case and 3-100 chars.", 400)
    if not display_name:
        return error_response("display_name is required.", 400)
    if rollout_percentage < 0 or rollout_percentage > 100:
        return error_response("rollout_percentage must be between 0 and 100.", 400)

    if FeatureFlag.query.filter_by(flag_key=flag_key).first():
        return error_response("Feature flag with this key already exists.", 409)

    flag = FeatureFlag(
        flag_key=flag_key,
        display_name=display_name,
        description=description,
        is_enabled=is_enabled,
        rollout_percentage=rollout_percentage,
        enabled_org_ids=[],
    )
    db.session.add(flag)
    _write_admin_audit("admin.flag_created", "feature_flag", None, extra_json={"flag_key": flag_key})
    db.session.commit()
    return success_response({"flag_key": flag.flag_key, "id": str(flag.id)}, status=201)


@admin_bp.get("/billing")
def admin_billing():
    """Render platform billing and revenue analytics page."""

    now = _utcnow()
    month_start = _start_of_month(now)

    month_bucket = _month_bucket(Invoice.paid_at)
    month_rows = (
        db.session.query(
            month_bucket.label("month"),
            func.sum(Invoice.amount_paise).label("amount_paise"),
        )
        .filter(Invoice.status == "paid", Invoice.paid_at.isnot(None), Invoice.paid_at >= now - timedelta(days=365))
        .group_by(month_bucket)
        .order_by(month_bucket.asc())
        .all()
    )
    mrr_by_month = []
    for row in month_rows:
        month_value = row.month
        if month_value is None:
            continue
        if hasattr(month_value, "strftime"):
            month_label = month_value.strftime("%b %Y")
        else:
            month_label = datetime.strptime(str(month_value)[:10], "%Y-%m-%d").strftime("%b %Y")
        mrr_by_month.append(
            {
                "month": month_label,
                "mrr": round((_safe_int(row.amount_paise) / 100), 2),
            }
        )

    active_subscriptions_rows = (
        Subscription.query.filter(Subscription.status == "active")
        .join(Plan, Subscription.plan_id == Plan.id)
        .all()
    )
    current_mrr_paise = sum(_monthly_equivalent_paise(sub, sub.plan) for sub in active_subscriptions_rows)
    current_mrr = round(current_mrr_paise / 100, 2)

    plan_distribution_rows = (
        db.session.query(Plan.name, func.count(Organization.id))
        .join(Organization, Organization.plan_id == Plan.id)
        .filter(Organization.is_deleted.is_(False))
        .group_by(Plan.name)
        .all()
    )
    plan_distribution = [{"plan": plan_name, "count": int(count)} for plan_name, count in plan_distribution_rows]

    recent_payments = (
        Invoice.query.join(Organization, Organization.id == Invoice.org_id)
        .outerjoin(Subscription, Subscription.id == Invoice.subscription_id)
        .filter(Invoice.status == "paid")
        .order_by(Invoice.paid_at.desc().nullslast(), Invoice.created_at.desc())
        .limit(20)
        .all()
    )

    failed_payments = (
        Invoice.query.join(Organization, Organization.id == Invoice.org_id)
        .filter(Invoice.status != "paid", Invoice.created_at >= now - timedelta(days=30))
        .order_by(Invoice.created_at.desc())
        .limit(20)
        .all()
    )

    cancelled_this_month = Subscription.query.filter(
        Subscription.cancelled_at.isnot(None),
        Subscription.cancelled_at >= month_start,
    ).count()
    active_at_start = Subscription.query.filter(
        Subscription.created_at < month_start,
        or_(Subscription.cancelled_at.is_(None), Subscription.cancelled_at >= month_start),
    ).count()
    churn_rate = round((cancelled_this_month / active_at_start) * 100, 2) if active_at_start else 0.0

    active_users = User.query.filter(User.is_active.is_(True)).count()
    arpu = round(current_mrr / active_users, 2) if active_users else 0.0

    total_revenue_paise = db.session.query(func.coalesce(func.sum(Invoice.amount_paise), 0)).filter(Invoice.status == "paid").scalar() or 0
    total_revenue = round(_safe_int(total_revenue_paise) / 100, 2)

    free_plan_user_count = (
        db.session.query(func.count(func.distinct(OrganizationMember.user_id)))
        .join(Organization, Organization.id == OrganizationMember.org_id)
        .join(Plan, Plan.id == Organization.plan_id)
        .filter(Organization.is_deleted.is_(False), Plan.slug == "free")
        .scalar()
        or 0
    )

    return render_template(
        "admin/admin_billing.html",
        mrr_by_month=mrr_by_month,
        mrr=current_mrr,
        plan_distribution=plan_distribution,
        recent_payments=recent_payments,
        failed_payments=failed_payments,
        churn_rate=churn_rate,
        arpu=arpu,
        total_revenue=total_revenue,
        free_plan_user_count=free_plan_user_count,
    )


@admin_bp.get("/system")
def admin_system():
    """Render system health and observability page."""

    metrics = collect_system_metrics()
    return render_template("admin/admin_system.html", metrics=metrics)
