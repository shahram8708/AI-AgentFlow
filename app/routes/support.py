"""Support, status, and legal blueprint routes."""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, text
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import AuditLog, SupportTicket
from app.services.auth_service import AuthService
from app.services.email_service import EmailService
from app.utils.decorators import login_required, org_required
from app.utils.response_helpers import error_response, success_response

support_bp = Blueprint("support", __name__)

auth_service = AuthService()
email_service = EmailService()


HELP_ARTICLES = [
    {
        "slug": "getting-started",
        "title": "Getting Started",
        "icon": "bi-rocket-takeoff",
        "articles": [
            "Create your first workspace",
            "Launch your first automation task",
            "Invite your team members",
            "Understand plans and quotas",
        ],
    },
    {
        "slug": "task-automation",
        "title": "Task Automation",
        "icon": "bi-lightning-charge",
        "articles": [
            "Choose the right task type",
            "Configure task input fields",
            "Track task run status in real time",
            "Export task outputs",
        ],
    },
    {
        "slug": "workflows",
        "title": "Workflows",
        "icon": "bi-diagram-3",
        "articles": [
            "Build reusable workflows",
            "Use workflow templates",
            "Schedule workflow runs",
            "Version and test workflow changes",
        ],
    },
    {
        "slug": "integrations",
        "title": "Integrations",
        "icon": "bi-plug",
        "articles": [
            "Connect Google Workspace",
            "Set up CRM integrations",
            "Manage API credentials securely",
            "Troubleshoot integration auth errors",
        ],
    },
    {
        "slug": "billing",
        "title": "Billing and Plans",
        "icon": "bi-credit-card",
        "articles": [
            "Compare Free, Starter, Pro, Team plans",
            "Upgrade and downgrade safely",
            "Understand invoice and GST details",
            "Cancel or renew subscriptions",
        ],
    },
    {
        "slug": "troubleshooting",
        "title": "Troubleshooting",
        "icon": "bi-tools",
        "articles": [
            "Task failures and retry strategy",
            "Queue delays and worker health",
            "API key and permission issues",
            "Performance best practices",
            "How to contact support",
        ],
    },
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@support_bp.get("/help")
def help_center():
    """Public help center page."""

    popular_articles = [
        "How to launch your first task",
        "Why my task is still running",
        "How usage quota works",
        "How to upgrade with Razorpay",
        "How to add integrations",
    ]
    return render_template(
        "support/help.html",
        categories=HELP_ARTICLES,
        popular_articles=popular_articles,
    )


@support_bp.get("/support")
@login_required
@org_required
def contact_support_page():
    """Render support ticket page for authenticated users."""

    tickets = (
        SupportTicket.query.filter_by(user_id=current_user.id, is_deleted=False)
        .order_by(SupportTicket.created_at.desc())
        .limit(10)
        .all()
    )

    status_rows = (
        db.session.query(SupportTicket.status, func.count(SupportTicket.id))
        .filter(SupportTicket.user_id == current_user.id, SupportTicket.is_deleted.is_(False))
        .group_by(SupportTicket.status)
        .all()
    )
    status_counts = {status: int(count) for status, count in status_rows}

    return render_template(
        "support/contact_support.html",
        tickets=tickets,
        status_counts=status_counts,
    )


@support_bp.post("/support")
@login_required
@org_required
def submit_support_ticket():
    """Create support ticket and notify support plus requester."""

    payload = request.get_json(silent=True) if request.is_json else request.form

    subject = str(payload.get("subject") or "").strip()
    description = str(payload.get("description") or "").strip()
    priority = str(payload.get("priority") or "medium").strip().lower()
    attachment_note = ""

    if not request.is_json:
        attachment = request.files.get("attachment")
        if attachment and attachment.filename:
            attachment.seek(0, 2)
            attachment_size = int(attachment.tell() or 0)
            attachment.seek(0)
            if attachment_size > 10 * 1024 * 1024:
                return error_response("Attachment exceeds 10MB limit.", 400)

            attachment_name = secure_filename(attachment.filename) or "attachment"
            size_kb = round(attachment_size / 1024, 1)
            attachment_note = f"\n\nAttachment note: {attachment_name} ({size_kb} KB)"

    if not subject:
        return error_response("Subject is required.", 400)
    if len(description) < 20:
        return error_response("Description must be at least 20 characters.", 400)
    if priority not in {"low", "medium", "high", "urgent"}:
        return error_response("Priority must be low, medium, high, or urgent.", 400)

    ticket = SupportTicket(
        user_id=current_user.id,
        org_id=g.org.id,
        subject=subject,
        description=f"{description}{attachment_note}",
        priority=priority,
        status="open",
    )
    db.session.add(ticket)

    db.session.add(
        AuditLog(
            org_id=g.org.id,
            user_id=current_user.id,
            action="support.ticket_created",
            resource_type="support_ticket",
            resource_id=str(ticket.id),
            ip_address=request.remote_addr,
            user_agent=(request.user_agent.string or "")[:500],
            extra_json={
                "priority": priority,
                "attachment_included": bool(attachment_note),
            },
        )
    )
    db.session.commit()

    support_address_raw = current_app.config.get("MAIL_DEFAULT_SENDER", "support@agentflow.ai")
    if isinstance(support_address_raw, (list, tuple)):
        support_address = str(support_address_raw[-1])
    else:
        support_address = str(support_address_raw)

    email_service.send_generic_email(
        to_email=support_address,
        subject=f"New Support Ticket: {subject}",
        template_name="emails/support_ticket_notification.html",
        context={
            "ticket": ticket,
            "user": current_user,
            "organization": g.org,
        },
    )

    email_service.send_generic_email(
        to_email=current_user.email,
        subject=f"Support Request Received: {subject}",
        template_name="emails/support_ticket_ack.html",
        context={
            "ticket": ticket,
            "user": current_user,
            "organization": g.org,
        },
    )

    return success_response({"ticket_id": str(ticket.id)})


@support_bp.route("/status", methods=["GET", "POST"])
def platform_status():
    """Public platform status page."""

    if request.method == "POST":
        flash("You are subscribed to status updates.", "success")
        return redirect(url_for("support.platform_status"))

    db_status = "operational"
    redis_status = "operational"
    task_queue_status = "operational"

    db_response_ms = None
    redis_response_ms = None

    start = perf_counter()
    try:
        db.session.execute(text("SELECT 1"))
        db_response_ms = round((perf_counter() - start) * 1000, 2)
    except Exception:  # pylint: disable=broad-except
        db_status = "degraded"

    redis_client = current_app.extensions.get("redis_client")
    queue_depth = 0
    if redis_client is not None:
        start = perf_counter()
        try:
            redis_client.ping()
            redis_response_ms = round((perf_counter() - start) * 1000, 2)
            queue_depth = int(redis_client.llen("default"))
            if queue_depth > 500:
                task_queue_status = "outage"
            elif queue_depth > 100:
                task_queue_status = "degraded"
        except Exception:  # pylint: disable=broad-except
            redis_status = "degraded"
    else:
        redis_status = "degraded"

    component_states = [db_status, redis_status, task_queue_status]
    if "outage" in component_states:
        overall_status = "Partial Outage"
    elif "degraded" in component_states:
        overall_status = "Degraded Performance"
    else:
        overall_status = "All Systems Operational"

    incidents = [
        {
            "title": "Increased Task Execution Latency",
            "status": "Resolved",
            "date": "Apr 2025",
            "affected": "Task Engine",
            "duration": "45 minutes",
        },
        {
            "title": "Database Connection Slowness",
            "status": "Resolved",
            "date": "Mar 2025",
            "affected": "Database",
            "duration": "20 minutes",
        },
        {
            "title": "API Timeouts",
            "status": "Resolved",
            "date": "Feb 2025",
            "affected": "API Service",
            "duration": "1 hour",
        },
    ]

    components = [
        {
            "name": "Web Application",
            "description": "Dashboard and app UI",
            "status": "operational" if db_status == "operational" else "degraded",
            "response_ms": db_response_ms,
        },
        {
            "name": "API Service",
            "description": "Public and internal APIs",
            "status": "operational" if redis_status == "operational" else "degraded",
            "response_ms": redis_response_ms,
        },
        {
            "name": "Task Execution Engine",
            "description": "Background task processing",
            "status": task_queue_status,
            "response_ms": None,
        },
        {
            "name": "Database",
            "description": "Primary PostgreSQL datastore",
            "status": db_status,
            "response_ms": db_response_ms,
        },
        {
            "name": "Background Workers",
            "description": "Celery workers and queues",
            "status": task_queue_status,
            "response_ms": None,
        },
    ]

    return render_template(
        "support/status.html",
        overall_status=overall_status,
        components=components,
        incidents=incidents,
        queue_depth=queue_depth,
    )


@support_bp.get("/privacy")
def privacy_policy():
    return render_template("legal/privacy.html")


@support_bp.get("/terms")
def terms_of_service():
    return render_template("legal/terms.html")


@support_bp.get("/cookies")
def cookie_policy():
    return render_template("legal/cookies.html")


@support_bp.route("/gdpr", methods=["GET", "POST"])
def gdpr_request_page():
    """Render and process GDPR data subject request form."""

    if request.method == "GET":
        return render_template("legal/gdpr.html")

    if not current_user.is_authenticated:
        flash("Please sign in to submit a GDPR request.", "warning")
        return redirect(url_for("auth.login", next="/gdpr"))

    request_type = str(request.form.get("request_type") or "").strip().lower()
    email = str(request.form.get("email") or "").strip().lower()
    details = str(request.form.get("details") or "").strip()

    if request_type == "correction":
        request_type = "rectification"

    if request_type not in {"access", "deletion", "portability", "rectification", "restriction"}:
        flash("Please select a valid GDPR request type.", "danger")
        return redirect(url_for("support.gdpr_request_page"))

    if email != (current_user.email or "").strip().lower():
        flash("Email confirmation must match your account email.", "danger")
        return redirect(url_for("support.gdpr_request_page"))

    organization = auth_service.get_user_org(current_user.id)
    if organization is None:
        flash("Unable to process request without an active organization.", "danger")
        return redirect(url_for("support.gdpr_request_page"))

    ticket = SupportTicket(
        user_id=current_user.id,
        org_id=organization.id,
        subject=f"GDPR Data Subject Request: {request_type}",
        description=details or f"GDPR request submitted for type: {request_type}",
        priority="high",
        status="open",
    )
    db.session.add(ticket)
    db.session.add(
        AuditLog(
            org_id=organization.id,
            user_id=current_user.id,
            action="gdpr.data_request_submitted",
            resource_type="support_ticket",
            resource_id=str(ticket.id),
            ip_address=request.remote_addr,
            user_agent=(request.user_agent.string or "")[:500],
            extra_json={"request_type": request_type},
        )
    )
    db.session.commit()

    email_service.send_generic_email(
        to_email=current_user.email,
        subject="GDPR Request Received",
        template_name="emails/gdpr_confirmation.html",
        context={
            "user": current_user,
            "request_type": request_type,
            "ticket": ticket,
        },
    )

    flash("Your GDPR request has been submitted successfully.", "success")
    return redirect(url_for("support.gdpr_request_page"))
