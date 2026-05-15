"""Application API blueprint routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import time
from uuid import UUID

import redis
from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    g,
    redirect,
    request,
    send_file,
    stream_with_context,
    url_for,
)
from flask_login import current_user

from app.extensions import cache
from app.extensions import db
from app.models import (
    AuditLog,
    AutomationTask,
    KnowledgeBaseEntry,
    Plan,
    Project,
    SupportTicket,
    TaskOutput,
    TaskStep,
    UsageRecord,
    Workflow,
)
from app.routes.admin import collect_system_metrics
from app.routes.dashboard import collect_dashboard_data
from app.routes.reports import _build_report_data, _resolve_date_range
from app.services.auth_service import AuthService
from app.services.export_service import ExportService, ExportServiceError
from app.services.file_service import FileService, FileServiceError
from app.services.notification_service import NotificationService
from app.tasks import celery
from app.utils.decorators import admin_required, login_required, org_required
from app.utils.response_helpers import error_response, success_response
from app.utils.validators import validate_uuid

api_bp = Blueprint("api", __name__, url_prefix="/api")

auth_service = AuthService()
notification_service = NotificationService()
export_service = ExportService()
file_service = FileService()


@api_bp.get("/dashboard/stats")
@login_required
@cache.cached(timeout=30, key_prefix=lambda: f"dashboard_stats_{current_user.id}")
def dashboard_stats():
    """Return dashboard stats for AJAX refresh."""

    org = auth_service.get_user_org(current_user.id)
    if org is None:
        return error_response("Organization not found.", status=403)

    context = collect_dashboard_data(org, current_user.id)

    payload = {
        "tasks_today": int(context.get("tasks_completed_today", 0)),
        "tasks_this_week": int(context.get("tasks_completed_this_week", 0)),
        "tasks_this_month": int(context.get("tasks_this_month", 0)),
        "tasks_running": int(context.get("tasks_running", 0)),
        "success_rate": float(context.get("success_rate", 100.0)),
        "quota_used": int(context.get("quota_used", 0)),
        "quota_limit": int(context.get("quota_limit", 0)),
        "quota_percent": float(context.get("quota_percent", 0.0)),
        "unread_notifications": int(context.get("unread_count", 0)),
        "last_updated": datetime.utcnow().isoformat(),
    }
    return success_response(payload)


@api_bp.get("/billing/usage-chart")
@login_required
@org_required
def billing_usage_chart():
    """Return 30 day daily task usage counts for billing chart."""

    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0)

    grouped_rows = (
        db.session.query(
            db.func.date(UsageRecord.recorded_at).label("usage_date"),
            db.func.coalesce(db.func.sum(UsageRecord.units_consumed), 0).label("count"),
        )
        .filter(
            UsageRecord.org_id == g.org.id,
            UsageRecord.usage_type == "task_run",
            UsageRecord.recorded_at >= start,
        )
        .group_by(db.func.date(UsageRecord.recorded_at))
        .all()
    )

    counts_map = {
        row.usage_date.strftime("%Y-%m-%d"): int(row.count or 0)
        for row in grouped_rows
        if row.usage_date is not None
    }

    daily_usage = []
    cursor = start
    for _ in range(30):
        key = cursor.strftime("%Y-%m-%d")
        daily_usage.append({"date": key, "count": counts_map.get(key, 0)})
        cursor += timedelta(days=1)

    quota_limit = -1
    if g.org.plan_id:
        plan = db.session.get(Plan, g.org.plan_id)
        if plan is not None:
            quota_limit = int(plan.task_quota_monthly)

    return success_response({"daily_usage": daily_usage, "points": daily_usage, "quota_limit": quota_limit})


@api_bp.get("/reports/chart-data")
@login_required
@org_required
def reports_chart_data():
    """Return report chart datasets and KPI values for AJAX refresh."""

    try:
        date_from, date_to, _period = _resolve_date_range()
    except ValueError as exc:
        return error_response(str(exc), 400)

    report_data = _build_report_data(g.org.id, date_from, date_to)

    return success_response(
        {
            "line_chart_data": report_data["line_chart_data"],
            "bar_chart_data": report_data["bar_chart_data"],
            "pie_chart_data": report_data["pie_chart_data"],
            "status_breakdown": report_data["status_breakdown"],
            "daily_success_rate": report_data["daily_success_rate"],
            "kpis": {
                "total_tasks": report_data["total_tasks"],
                "completed_tasks": report_data["completed_tasks"],
                "failed_tasks": report_data["failed_tasks"],
                "success_rate": report_data["success_rate"],
                "avg_duration_seconds": report_data["avg_duration_seconds"],
                "total_outputs_generated": report_data["total_outputs_generated"],
                "tasks_vs_previous_period": report_data["tasks_vs_previous_period"],
                "completed_vs_previous_period": report_data["completed_vs_previous_period"],
                "failed_vs_previous_period": report_data["failed_vs_previous_period"],
            },
        }
    )


@api_bp.get("/admin/system-metrics")
@admin_required
@cache.cached(timeout=10, key_prefix="admin_system_metrics")
def admin_system_metrics_api():
    """Return near real-time platform system metrics for admin refresh."""

    metrics = collect_system_metrics()
    return success_response(metrics)


@api_bp.get("/support/tickets/<ticket_id>")
@login_required
def support_ticket_details(ticket_id: str):
    """Return support ticket details for ticket detail modal rendering."""

    if not validate_uuid(ticket_id):
        return error_response("Invalid ticket ID", 400)

    ticket = SupportTicket.query.filter_by(id=UUID(ticket_id), is_deleted=False).first()
    if ticket is None:
        return error_response("Ticket not found", 404)

    if ticket.user_id != current_user.id and not current_user.is_admin:
        return error_response("Unauthorized", 403)

    return success_response(
        {
            "id": str(ticket.id),
            "subject": ticket.subject,
            "description": ticket.description,
            "priority": ticket.priority,
            "status": ticket.status,
            "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
            "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
        }
    )


@api_bp.get("/notifications/count")
@login_required
def notifications_count():
    """Return unread notifications count for topbar bell."""

    org = auth_service.get_user_org(current_user.id)
    if org is None:
        return error_response("Organization not found.", status=403)

    unread_count = notification_service.get_unread_count(current_user.id, org.id)
    return success_response({"unread_count": int(unread_count)})


@api_bp.route("/tasks/<task_id>/stream")
@login_required
def task_stream(task_id: str):
    """Server Sent Events endpoint for live task execution updates."""

    if not validate_uuid(task_id):
        return error_response("Invalid task ID", 400)

    task_uuid = UUID(task_id)
    task = AutomationTask.query.get(task_uuid)
    if task is None:
        return error_response("Task not found", 404)

    org = auth_service.get_user_org(current_user.id)
    if not org or str(task.org_id) != str(org.id):
        return error_response("Unauthorized", 403)

    @stream_with_context
    def generate():
        redis_client = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
        pubsub = redis_client.pubsub()
        channel = f"task_log:{task_id}"
        pubsub.subscribe(channel)

        yield (
            f"data: {json.dumps({'type': 'connected', 'task_id': task_id, 'message': 'Connected to task stream'})}\n\n"
        )

        current_status = AutomationTask.query.get(task_uuid)
        if current_status:
            yield (
                f"data: {json.dumps({'type': 'status', 'status': current_status.status, 'task_id': task_id})}\n\n"
            )
            if current_status.status in {"done", "failed", "cancelled"}:
                yield (
                    f"data: {json.dumps({'type': 'final', 'status': current_status.status, 'redirect_url': f'/tasks/{task_id}/result' if current_status.status == 'done' else None})}\n\n"
                )
                try:
                    pubsub.unsubscribe(channel)
                    pubsub.close()
                    redis_client.close()
                except Exception:  # pylint: disable=broad-except
                    pass
                return

        timeout_seconds = 960
        start_time = time.time()

        try:
            for message in pubsub.listen():
                if time.time() - start_time > timeout_seconds:
                    yield f"data: {json.dumps({'type': 'timeout', 'message': 'Stream timeout'})}\n\n"
                    break

                if message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")

                    try:
                        event_obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    yield f"data: {json.dumps(event_obj)}\n\n"

                    if event_obj.get("level") in ("complete", "failed"):
                        final_task = AutomationTask.query.get(task_uuid)
                        if final_task:
                            yield (
                                f"data: {json.dumps({'type': 'final', 'status': final_task.status, 'redirect_url': f'/tasks/{task_id}/result' if final_task.status == 'done' else None})}\n\n"
                            )
                        break

        except GeneratorExit:
            pass
        except Exception as exc:  # pylint: disable=broad-except
            current_app.logger.error("SSE stream error for task %s: %s", task_id, exc)
            yield f"data: {json.dumps({'type': 'error', 'message': 'Stream error'})}\n\n"
        finally:
            try:
                pubsub.unsubscribe(channel)
                pubsub.close()
                redis_client.close()
            except Exception:  # pylint: disable=broad-except
                pass

    response = Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
            "Connection": "keep-alive",
        },
    )
    return response


@api_bp.route("/tasks/<task_id>/status")
@login_required
def task_status(task_id: str):
    """Return current task state and step progress details as JSON."""

    if not validate_uuid(task_id):
        return error_response("Invalid task ID", 400)

    task = AutomationTask.query.get(UUID(task_id))
    if task is None:
        return error_response("Task not found", 404)

    org = auth_service.get_user_org(current_user.id)
    if not org or str(task.org_id) != str(org.id):
        return error_response("Unauthorized", 403)

    steps = (
        TaskStep.query.filter_by(task_id=task.id)
        .order_by(TaskStep.step_number.asc())
        .all()
    )

    duration_seconds = 0
    if task.started_at and task.completed_at:
        duration_seconds = int((task.completed_at - task.started_at).total_seconds())
    elif task.started_at:
        now_value = datetime.utcnow()
        if task.started_at.tzinfo is not None:
            now_value = datetime.now(task.started_at.tzinfo)
        duration_seconds = int((now_value - task.started_at).total_seconds())

    steps_payload = [
        {
            "step_number": step.step_number,
            "step_name": step.step_name,
            "status": step.status,
            "duration_ms": step.duration_ms,
            "started_at": step.started_at.isoformat() if step.started_at else None,
            "completed_at": step.completed_at.isoformat() if step.completed_at else None,
        }
        for step in steps
    ]

    steps_completed = sum(1 for step in steps if step.status == "done")
    has_output = (
        TaskOutput.query.filter_by(task_id=task.id, is_deleted=False).first() is not None
    )

    payload = {
        "task_id": str(task.id),
        "status": task.status,
        "task_name": task.task_name,
        "task_type": task.task_type,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "duration_seconds": duration_seconds,
        "error_message": task.error_message,
        "steps": steps_payload,
        "steps_completed": steps_completed,
        "steps_total": len(steps),
        "has_output": has_output,
        "result_url": f"/tasks/{task.id}/result",
    }
    return success_response(payload)


@api_bp.route("/tasks/<task_id>/cancel", methods=["POST"])
@login_required
def cancel_task(task_id: str):
    """Cancel a pending or running task."""

    if not validate_uuid(task_id):
        return error_response("Invalid task ID", 400)

    task = AutomationTask.query.get(UUID(task_id))
    if task is None:
        return error_response("Task not found", 404)

    org = auth_service.get_user_org(current_user.id)
    if not org or str(task.org_id) != str(org.id):
        return error_response("Unauthorized", 403)

    if task.status not in ["pending", "running"]:
        return error_response("Task cannot be cancelled in its current state", 400)

    try:
        if task.celery_task_id and task.status == "running":
            celery.control.revoke(task.celery_task_id, terminate=True, signal="SIGKILL")

        task.status = "cancelled"
        task.error_message = "Cancelled by user"
        task.completed_at = datetime.utcnow()

        TaskStep.query.filter_by(task_id=task.id, status="running").update(
            {
                "status": "failed",
                "error_msg": "Cancelled by user",
                "completed_at": datetime.utcnow(),
            },
            synchronize_session=False,
        )

        audit_log = AuditLog(
            org_id=task.org_id,
            user_id=current_user.id,
            action="task.cancelled",
            resource_type="task",
            resource_id=str(task.id),
            extra_json={
                "task_type": task.task_type,
                "status": "cancelled",
            },
        )
        db.session.add(audit_log)
        db.session.commit()

        try:
            redis_client = redis.Redis.from_url(
                os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            )
            redis_client.publish(
                f"task_log:{task_id}",
                json.dumps(
                    {
                        "type": "log",
                        "timestamp": datetime.utcnow().isoformat(),
                        "level": "failed",
                        "message": "Task cancelled by user",
                    }
                ),
            )
            redis_client.close()
        except Exception:  # pylint: disable=broad-except
            pass

        return success_response({"cancelled": True, "task_id": task_id})
    except Exception as exc:  # pylint: disable=broad-except
        db.session.rollback()
        return error_response(f"Failed to cancel task: {exc}", 500)


@api_bp.route("/outputs/<output_id>/download")
@login_required
def download_output(output_id: str):
    """Download task output in requested export format."""

    if not validate_uuid(output_id):
        return error_response("Invalid output ID", 400)

    fmt = request.args.get("format", "json")
    output = TaskOutput.query.filter_by(id=UUID(output_id), is_deleted=False).first()
    if output is None:
        return error_response("Output not found", 404)

    task = AutomationTask.query.get(output.task_id)
    if task is None:
        return error_response("Task not found", 404)

    org = auth_service.get_user_org(current_user.id)
    if not org or str(task.org_id) != str(org.id):
        return error_response("Unauthorized", 403)

    try:
        if not output.content_text and output.file_path:
            output.content_text = file_service.read_output_file(output.file_path)

        buffer, mimetype, filename = export_service.get_content_for_format(task, output, fmt)
        buffer.seek(0)
        return send_file(
            buffer,
            mimetype=mimetype,
            as_attachment=True,
            download_name=filename,
        )
    except (ExportServiceError, FileServiceError) as exc:
        flash(f"Could not prepare download: {exc}", "danger")
        return redirect(url_for("tasks.task_result", task_id=task.id))


@api_bp.route("/outputs/<output_id>/notes", methods=["POST"])
@login_required
def save_output_note(output_id: str):
    """Persist an internal note for a generated output."""

    if not validate_uuid(output_id):
        return error_response("Invalid output ID", 400)

    output = TaskOutput.query.filter_by(id=UUID(output_id), is_deleted=False).first()
    if output is None:
        return error_response("Output not found", 404)

    task = AutomationTask.query.get(output.task_id)
    if task is None:
        return error_response("Task not found", 404)

    org = auth_service.get_user_org(current_user.id)
    if not org or str(task.org_id) != str(org.id):
        return error_response("Unauthorized", 403)

    payload = request.get_json(silent=True) or {}
    note_text = str(payload.get("note") or request.form.get("note") or "").strip()
    if not note_text:
        return error_response("Note cannot be empty", 400)

    existing_input = dict(task.input_json or {})
    notes = list(existing_input.get("notes") or [])
    notes.append(
        {
            "note": note_text[:2000],
            "user_id": str(current_user.id),
            "created_at": datetime.utcnow().isoformat(),
        }
    )
    existing_input["notes"] = notes
    task.input_json = existing_input
    db.session.commit()

    return success_response({"saved": True, "notes": notes})


@api_bp.route("/outputs/<output_id>/save", methods=["POST"])
@login_required
def save_output_to_project(output_id: str):
    """Associate output's task to a project for organization level tracking."""

    if not validate_uuid(output_id):
        return error_response("Invalid output ID", 400)

    output = TaskOutput.query.filter_by(id=UUID(output_id), is_deleted=False).first()
    if output is None:
        return error_response("Output not found", 404)

    task = AutomationTask.query.get(output.task_id)
    if task is None:
        return error_response("Task not found", 404)

    org = auth_service.get_user_org(current_user.id)
    if not org or str(task.org_id) != str(org.id):
        return error_response("Unauthorized", 403)

    payload = request.get_json(silent=True) or {}
    project_id = str(payload.get("project_id") or "").strip()
    note = str(payload.get("note") or "").strip()

    if not validate_uuid(project_id):
        return error_response("Invalid project ID", 400)

    project = Project.query.filter_by(
        id=UUID(project_id),
        org_id=org.id,
        is_deleted=False,
    ).first()
    if project is None:
        return error_response("Project not found", 404)

    existing_input = dict(task.input_json or {})
    if note:
        project_notes = list(existing_input.get("project_notes") or [])
        project_notes.append(
            {
                "project_id": str(project.id),
                "note": note[:2000],
                "saved_by": str(current_user.id),
                "saved_at": datetime.utcnow().isoformat(),
            }
        )
        existing_input["project_notes"] = project_notes
        task.input_json = existing_input

    task.project_id = project.id
    db.session.commit()

    return success_response({"saved": True, "project_id": str(project.id)})


@api_bp.get("/search/suggestions")
@login_required
@org_required
def search_suggestions():
    """Return real-time search suggestions for the global topbar search."""

    query = request.args.get("q", "").strip()
    if not query or len(query) < 2:
        return success_response({"results": []})

    if len(query) > 100:
        return error_response("Query too long.", 400)

    limit = 5
    results = []

    # Tasks
    task_rows = (
        AutomationTask.query.filter(
            AutomationTask.org_id == g.org.id,
            AutomationTask.task_name.ilike(f"%{query}%"),
        )
        .order_by(AutomationTask.created_at.desc())
        .limit(limit)
        .all()
    )
    for t in task_rows:
        status_map = {
            "done": "success",
            "failed": "danger",
            "running": "primary",
            "pending": "warning",
        }
        badge = status_map.get(t.status, "secondary")
        results.append(
            {
                "type": "task",
                "icon": "bi-lightning-charge",
                "label": t.task_name or t.task_type,
                "sublabel": t.status.capitalize(),
                "badge_color": badge,
                "url": f"/tasks/{t.id}",
            }
        )

    # Workflows
    workflow_rows = (
        Workflow.query.filter(
            Workflow.org_id == g.org.id,
            Workflow.is_deleted.is_(False),
            Workflow.name.ilike(f"%{query}%"),
        )
        .order_by(Workflow.created_at.desc())
        .limit(limit)
        .all()
    )
    for w in workflow_rows:
        results.append(
            {
                "type": "workflow",
                "icon": "bi-diagram-3",
                "label": w.name,
                "sublabel": "Workflow",
                "badge_color": "info",
                "url": f"/workflows/{w.id}",
            }
        )

    # Projects
    project_rows = (
        Project.query.filter(
            Project.org_id == g.org.id,
            Project.is_deleted.is_(False),
            Project.name.ilike(f"%{query}%"),
        )
        .order_by(Project.created_at.desc())
        .limit(limit)
        .all()
    )
    for p in project_rows:
        results.append(
            {
                "type": "project",
                "icon": "bi-folder2-open",
                "label": p.name,
                "sublabel": "Project",
                "badge_color": "secondary",
                "url": f"/projects/{p.id}",
            }
        )

    # Outputs
    output_rows = (
        TaskOutput.query.filter(
            TaskOutput.org_id == g.org.id,
            TaskOutput.is_deleted.is_(False),
            TaskOutput.file_name.ilike(f"%{query}%"),
        )
        .order_by(TaskOutput.created_at.desc())
        .limit(limit)
        .all()
    )
    for o in output_rows:
        results.append(
            {
                "type": "output",
                "icon": "bi-file-earmark-text",
                "label": o.file_name or "Output",
                "sublabel": "Output",
                "badge_color": "secondary",
                "url": f"/outputs/{o.id}",
            }
        )

    # Knowledge base
    kb_rows = (
        KnowledgeBaseEntry.query.filter(
            KnowledgeBaseEntry.org_id == g.org.id,
            KnowledgeBaseEntry.is_deleted.is_(False),
            KnowledgeBaseEntry.title.ilike(f"%{query}%"),
        )
        .order_by(KnowledgeBaseEntry.created_at.desc())
        .limit(limit)
        .all()
    )
    for k in kb_rows:
        results.append(
            {
                "type": "knowledge",
                "icon": "bi-book",
                "label": k.title,
                "sublabel": "Knowledge",
                "badge_color": "success",
                "url": f"/knowledge/{k.id}",
            }
        )

    # Deduplicate by url and cap total at 10
    seen = set()
    unique_results = []
    for item in results:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique_results.append(item)
        if len(unique_results) >= 10:
            break

    return success_response(
        {
            "results": unique_results,
            "query": query,
            "see_all_url": f"/tasks?search={query}",
        }
    )


@api_bp.get("/projects/<project_id>/stats")
@login_required
@org_required
def project_stats(project_id: str):
    """Return project statistics for dashboard widgets."""

    if not validate_uuid(project_id):
        return error_response("Invalid project ID", 400)

    project = Project.query.filter_by(
        id=UUID(project_id),
        org_id=g.org.id,
        is_deleted=False,
    ).first()
    if project is None:
        return error_response("Project not found", 404)

    task_count = AutomationTask.query.filter_by(project_id=project.id).count()
    workflow_count = Workflow.query.filter_by(project_id=project.id, is_deleted=False).count()
    output_count = (
        TaskOutput.query.join(AutomationTask, TaskOutput.task_id == AutomationTask.id)
        .filter(
            AutomationTask.project_id == project.id,
            TaskOutput.is_deleted.is_(False),
        )
        .count()
    )
    knowledge_count = KnowledgeBaseEntry.query.filter_by(
        project_id=project.id,
        is_deleted=False,
    ).count()

    tasks_completed = AutomationTask.query.filter_by(project_id=project.id, status="done").count()
    tasks_failed = AutomationTask.query.filter_by(project_id=project.id, status="failed").count()

    last_task_created = (
        db.session.query(db.func.max(AutomationTask.created_at))
        .filter(AutomationTask.project_id == project.id)
        .scalar()
    )
    last_task_updated = (
        db.session.query(db.func.max(AutomationTask.updated_at))
        .filter(AutomationTask.project_id == project.id)
        .scalar()
    )
    last_activity_candidates = [dt for dt in [last_task_created, last_task_updated] if dt]
    last_activity = max(last_activity_candidates).isoformat() if last_activity_candidates else None

    return success_response(
        {
            "task_count": int(task_count),
            "workflow_count": int(workflow_count),
            "output_count": int(output_count),
            "knowledge_count": int(knowledge_count),
            "tasks_completed": int(tasks_completed),
            "tasks_failed": int(tasks_failed),
            "last_activity": last_activity,
        }
    )
