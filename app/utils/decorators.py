"""Custom decorators used by application views."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, TypeVar
from urllib.parse import quote

from flask import abort, flash, g, jsonify, redirect, request, session, url_for
from flask_login import current_user, logout_user

from app.extensions import db
from app.models import Plan, UsageRecord
from app.services.auth_service import AuthService

F = TypeVar("F", bound=Callable[..., Any])

auth_service = AuthService()


def _is_json_request() -> bool:
    """Return True when request expects JSON response payloads."""

    return (
        request.path.startswith("/api")
        or request.is_json
        or request.accept_mimetypes.best == "application/json"
    )


def login_required(view_func: F) -> F:
    """Require authenticated and active users for route access.

    For JSON requests this returns a JSON 401 response.
    For HTML requests this redirects to login with a safe next parameter.
    """

    @wraps(view_func)
    def wrapper(*args: Any, **kwargs: Any):
        if not current_user.is_authenticated:
            if _is_json_request():
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "auth_required",
                            "message": "Please sign in to access this resource.",
                        }
                    ),
                    401,
                )

            session["next"] = request.url
            flash("Please sign in to access this page.", "warning")
            encoded_next = quote(request.url, safe="")
            return redirect(f"{url_for('auth.login')}?next={encoded_next}")

        if not getattr(current_user, "is_active", False):
            logout_user()
            session.clear()
            if _is_json_request():
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "account_disabled",
                            "message": "Your account has been disabled.",
                        }
                    ),
                    403,
                )
            flash("Your account has been disabled.", "danger")
            return redirect(url_for("auth.login"))

        return view_func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def org_required(view_func: F) -> F:
    """Require an active organization and inject it into flask.g."""

    @wraps(view_func)
    @login_required
    def wrapper(*args: Any, **kwargs: Any):
        organization = auth_service.get_user_org(current_user.id)
        if organization is None:
            if _is_json_request():
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "organization_required",
                            "message": "Please complete your account setup.",
                        }
                    ),
                    403,
                )
            flash("Please complete your account setup.", "warning")
            return redirect("/settings/account")

        if organization.is_deleted:
            if _is_json_request():
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "organization_deleted",
                            "message": "Your organization no longer exists. Please contact support.",
                        }
                    ),
                    403,
                )
            flash("Your organization no longer exists. Please contact support.", "danger")
            return redirect("/")

        g.org = organization
        g.current_org = organization
        return view_func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def admin_required(view_func: F) -> F:
    """Require an authenticated user with admin role."""

    @wraps(view_func)
    @login_required
    def wrapper(*args: Any, **kwargs: Any):
        if getattr(current_user, "role", "") != "admin":
            abort(403)
        return view_func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def quota_check(view_func: F) -> F:
    """Enforce monthly task quota for organization scoped task execution routes."""

    @wraps(view_func)
    @login_required
    @org_required
    def wrapper(*args: Any, **kwargs: Any):
        organization = g.org
        plan = db.session.get(Plan, organization.plan_id) if organization.plan_id else None
        quota = 10 if plan is None else plan.task_quota_monthly

        if quota == -1:
            g.quota_used = 0
            g.quota_limit = -1
            return view_func(*args, **kwargs)

        now = datetime.now(timezone.utc)
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        usage_count = (
            UsageRecord.query.filter(
                UsageRecord.org_id == organization.id,
                UsageRecord.usage_type == "task_run",
                UsageRecord.recorded_at >= period_start,
            ).count()
        )

        g.quota_used = usage_count
        g.quota_limit = quota

        if usage_count >= quota:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "quota_exceeded",
                        "message": "You have reached your monthly task limit.",
                        "quota": quota,
                        "used": usage_count,
                        "upgrade_url": "/settings/billing",
                    }
                ),
                403,
            )

        return view_func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


login_required_custom = login_required
