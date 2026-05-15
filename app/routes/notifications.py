"""Notification center blueprint routes."""

from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request
from flask_login import current_user
from sqlalchemy import func

from app.models import Notification
from app.services.auth_service import AuthService
from app.services.notification_service import NotificationService
from app.utils.decorators import login_required
from app.utils.response_helpers import error_response, success_response

notifications_bp = Blueprint("notifications", __name__)

auth_service = AuthService()
notification_service = NotificationService()


def _get_current_org():
    organization = auth_service.get_user_org(current_user.id)
    if organization is not None:
        g.org = organization
    return organization


@notifications_bp.get("/notifications")
@login_required
def notifications_home():
    """Render the notifications center page."""

    org = _get_current_org()
    if org is None:
        flash("Please complete your organization setup.", "warning")
        return redirect("/settings/account")

    current_type = (request.args.get("type") or "").strip().lower() or None
    unread_filter = str(request.args.get("unread", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    page = request.args.get("page", default=1, type=int)

    if current_type == "tasks":
        query = Notification.query.filter(
            Notification.user_id == current_user.id,
            Notification.org_id == org.id,
            Notification.is_deleted.is_(False),
            Notification.type.in_(["task_complete", "task_failed"]),
        )
        if unread_filter:
            query = query.filter(Notification.is_read.is_(False))
        notifications = query.order_by(Notification.created_at.desc()).paginate(
            page=max(page, 1),
            per_page=20,
            error_out=False,
        )
    else:
        notifications = notification_service.get_notifications(
            current_user.id,
            org.id,
            page=page,
            per_page=20,
            notification_type=current_type,
            unread_only=unread_filter,
        )

    unread_count = notification_service.get_unread_count(current_user.id, org.id)

    type_counts = {
        "all": 0,
        "unread": 0,
        "task_complete": 0,
        "task_failed": 0,
        "billing": 0,
        "team": 0,
        "system": 0,
        "quota_warning": 0,
    }

    try:
        grouped_counts = (
            Notification.query.with_entities(Notification.type, func.count(Notification.id))
            .filter_by(user_id=current_user.id, org_id=org.id, is_deleted=False)
            .group_by(Notification.type)
            .all()
        )
        for notif_type, count in grouped_counts:
            type_counts[notif_type] = int(count)
            type_counts["all"] += int(count)

        type_counts["unread"] = (
            Notification.query.filter_by(
                user_id=current_user.id,
                org_id=org.id,
                is_read=False,
                is_deleted=False,
            ).count()
        )
    except Exception:
        pass

    return render_template(
        "app/notifications.html",
        notifications=notifications,
        unread_count=unread_count,
        current_filter="unread" if unread_filter else "all",
        current_type=current_type,
        type_counts=type_counts,
    )


@notifications_bp.post("/notifications/<uuid:notification_id>/read")
@login_required
def mark_notification_read(notification_id):
    """Mark a single notification as read for the current user."""

    marked = notification_service.mark_as_read(notification_id, current_user.id)
    if not marked:
        return error_response("Notification not found.", status=404)
    return success_response({"marked_read": True})


@notifications_bp.post("/notifications/read-all")
@login_required
def mark_all_notifications_read():
    """Mark all notifications as read for current user and organization."""

    org = _get_current_org()
    if org is None:
        if request.is_json:
            return error_response("Organization not found.", status=403)
        flash("Please complete your organization setup.", "warning")
        return redirect("/settings/account")

    updated_count = notification_service.mark_all_as_read(current_user.id, org.id)
    flash("All notifications marked as read.", "success")

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if request.is_json or is_ajax:
        return success_response({"count": updated_count})
    return redirect("/notifications")


@notifications_bp.delete("/notifications/<uuid:notification_id>")
@login_required
def delete_notification(notification_id):
    """Soft delete a user owned notification."""

    deleted = notification_service.delete_notification(notification_id, current_user.id)
    if not deleted:
        return error_response("Notification not found.", status=404)
    return success_response({"deleted": True})
