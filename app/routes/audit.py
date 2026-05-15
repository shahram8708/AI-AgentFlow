"""Audit log blueprint routes."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

import pytz
from flask import Blueprint, abort, g, render_template, request, send_file
from flask_login import current_user

from app.models import AuditLog, OrganizationMember, User
from app.utils.decorators import login_required, org_required

audit_bp = Blueprint("audit", __name__)

IST = pytz.timezone("Asia/Kolkata")


def _parse_date(value: str, label: str) -> datetime:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Invalid {label}. Use YYYY-MM-DD format.") from exc
    return parsed.replace(tzinfo=timezone.utc)


def _to_ist_string(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IST).strftime("%d %b %Y, %I:%M:%S %p IST")


def _can_view_audit_log() -> bool:
    if current_user.is_admin:
        return True
    if g.org.owner_id == current_user.id:
        return True

    membership = OrganizationMember.query.filter_by(org_id=g.org.id, user_id=current_user.id).first()
    return bool(membership and membership.role == "admin")


@audit_bp.get("/audit-log")
@login_required
@org_required
def audit_log_home():
    """Render immutable audit log with filters and export support."""

    if not _can_view_audit_log():
        abort(403)

    now_utc = datetime.now(timezone.utc)
    default_from = (now_utc - timedelta(days=30)).date().strftime("%Y-%m-%d")
    default_to = now_utc.date().strftime("%Y-%m-%d")

    user_id_filter = (request.args.get("user_id") or "").strip()
    action_filter = (request.args.get("action") or "").strip().lower()
    search_filter = (request.args.get("search") or "").strip()
    date_from_raw = (request.args.get("date_from") or default_from).strip()
    date_to_raw = (request.args.get("date_to") or default_to).strip()

    try:
        date_from = _parse_date(date_from_raw, "date_from")
        date_to = _parse_date(date_to_raw, "date_to") + timedelta(days=1) - timedelta(microseconds=1)
    except ValueError as exc:
        abort(400, description=str(exc))

    if date_from > date_to:
        abort(400, description="date_from must be before date_to.")

    query = AuditLog.query.filter(AuditLog.org_id == g.org.id)

    if user_id_filter:
        query = query.filter(AuditLog.user_id == user_id_filter)
    if action_filter:
        query = query.filter(AuditLog.action.ilike(f"{action_filter}.%"))
    if search_filter:
        query = query.filter(AuditLog.action.ilike(f"%{search_filter}%"))

    query = query.filter(AuditLog.timestamp >= date_from, AuditLog.timestamp <= date_to)

    distinct_actions = (
        AuditLog.query.with_entities(AuditLog.action)
        .filter(AuditLog.org_id == g.org.id)
        .distinct()
        .all()
    )
    action_prefixes = sorted(
        {
            action.split(".", 1)[0]
            for (action,) in distinct_actions
            if action and "." in action
        }
    )

    users = (
        User.query.join(AuditLog, AuditLog.user_id == User.id)
        .filter(AuditLog.org_id == g.org.id)
        .distinct(User.id)
        .order_by(User.first_name.asc(), User.last_name.asc())
        .all()
    )

    export_kind = (request.args.get("export") or "").strip().lower()
    if export_kind == "csv":
        limit = 10000
        rows = query.order_by(AuditLog.timestamp.desc()).limit(limit + 1).all()
        truncated = len(rows) > limit
        if truncated:
            rows = rows[:limit]

        output = io.StringIO()
        writer = csv.writer(output)
        if truncated:
            writer.writerow(["Note: Export limited to 10,000 most recent records."])
            writer.writerow([])

        writer.writerow(
            [
                "Timestamp (IST)",
                "User Name",
                "User Email",
                "Action",
                "Resource Type",
                "Resource ID",
                "IP Address",
            ]
        )

        for entry in rows:
            writer.writerow(
                [
                    _to_ist_string(entry.timestamp),
                    entry.user.get_full_name() if entry.user else "System",
                    entry.user.email if entry.user else "-",
                    entry.action,
                    entry.resource_type or "",
                    entry.resource_id or "",
                    entry.ip_address or "",
                ]
            )

        data = io.BytesIO(output.getvalue().encode("utf-8-sig"))
        data.seek(0)
        filename = f"audit_log_{g.org.slug}_{datetime.now(timezone.utc).date()}.csv"
        return send_file(data, mimetype="text/csv", as_attachment=True, download_name=filename)

    page = request.args.get("page", default=1, type=int)
    pagination = query.order_by(AuditLog.timestamp.desc()).paginate(page=page, per_page=50, error_out=False)

    total_all = AuditLog.query.filter(AuditLog.org_id == g.org.id).count()

    return render_template(
        "app/audit_log.html",
        pagination=pagination,
        entries=pagination.items,
        action_prefixes=action_prefixes,
        users=users,
        selected_user_id=user_id_filter,
        selected_action=action_filter,
        selected_search=search_filter,
        date_from=date_from_raw,
        date_to=date_to_raw,
        total_filtered=pagination.total,
        total_all=total_all,
    )
