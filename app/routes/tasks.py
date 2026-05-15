"""Tasks blueprint routes for launcher, configuration, execution, and history."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
import io
import json
from typing import Any
from uuid import UUID

from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import Text, cast, or_

from app.extensions import db
from app.models import (
    AuditLog,
    AutomationTask,
    DataSource,
    KnowledgeBaseEntry,
    Plan,
    Project,
    TaskOutput,
    TaskStep,
    WorkflowTemplate,
)
from app.services.agent_runner import (
    CATEGORY_COLORS,
    CATEGORY_META,
    TASK_REGISTRY,
    get_all_categories,
    get_task_config,
)
from app.services.export_service import export_service
from app.services.file_service import FileServiceError, file_service
from app.tasks import celery
from app.utils.decorators import login_required, org_required, quota_check
from app.utils.response_helpers import error_response, success_response
from app.utils.validators import (
    sanitize_task_inputs,
    validate_task_input_data,
    validate_task_type,
    validate_uuid,
)

tasks_bp = Blueprint("tasks", __name__)


def _is_json_request() -> bool:
    return (
        request.path.startswith("/api")
        or request.is_json
        or request.accept_mimetypes.best == "application/json"
    )


def _task_lookup(task_id: str) -> AutomationTask | None:
    if not validate_uuid(task_id):
        return None
    return AutomationTask.query.filter_by(id=UUID(task_id), org_id=g.org.id).first()


def _load_recently_used_tasks(user_id: Any) -> list[dict[str, str]]:
    recent_rows = (
        AutomationTask.query.filter_by(user_id=user_id)
        .order_by(AutomationTask.created_at.desc())
        .limit(50)
        .all()
    )
    seen: set[str] = set()
    recently_used: list[dict[str, str]] = []
    for row in recent_rows:
        task_type = row.task_type
        if task_type in seen:
            continue
        if task_type not in TASK_REGISTRY:
            continue
        seen.add(task_type)
        task_config = TASK_REGISTRY[task_type]
        recently_used.append(
            {
                "task_type": task_type,
                "name": task_config.get("name", task_type),
                "category": task_config.get("category_display", task_config.get("category", "")),
            }
        )
        if len(recently_used) >= 5:
            break
    return recently_used


def _normalize_features(features_json: Any) -> set[str]:
    if isinstance(features_json, list):
        return {str(item).strip().lower() for item in features_json}
    if isinstance(features_json, dict):
        return {str(key).strip().lower() for key in features_json.keys()}
    return set()


def _category_counts() -> dict[str, int]:
    counts: dict[str, int] = {"all": len(TASK_REGISTRY)}
    for task in TASK_REGISTRY.values():
        category = task.get("category", "uncategorized")
        counts[category] = counts.get(category, 0) + 1
    return counts


@tasks_bp.get("/tasks/new")
@login_required
@org_required
def task_launcher():
    """Task launcher page with all registered task definitions."""

    categories = get_all_categories()
    recently_used = _load_recently_used_tasks(current_user.id)
    recommended_templates = (
        WorkflowTemplate.query.filter_by(is_featured=True, is_active=True)
        .order_by(WorkflowTemplate.created_at.desc())
        .limit(3)
        .all()
    )

    return render_template(
        "app/task_launcher.html",
        task_registry=TASK_REGISTRY,
        categories=categories,
        category_counts=_category_counts(),
        category_meta=CATEGORY_META,
        category_colors=CATEGORY_COLORS,
        recently_used=recently_used,
        recommended_templates=recommended_templates,
        total_tasks=len(TASK_REGISTRY),
    )


@tasks_bp.get("/tasks/configure/<task_type>")
@login_required
@org_required
def task_configure(task_type: str):
    """Task configuration wizard page."""

    if not validate_task_type(task_type):
        flash("Unknown task type.", "danger")
        return redirect(url_for("tasks.task_launcher"))

    config = get_task_config(task_type)
    projects = Project.query.filter_by(
        org_id=g.org.id,
        is_deleted=False,
        is_archived=False,
    ).order_by(Project.name.asc()).all()

    integrations = DataSource.query.filter_by(
        org_id=g.org.id,
        is_active=True,
        is_deleted=False,
    ).order_by(DataSource.name.asc()).all()

    matching_templates = (
        WorkflowTemplate.query.filter(
            WorkflowTemplate.is_active.is_(True),
            cast(WorkflowTemplate.steps_json, Text).ilike(f'%"task_type": "{task_type}"%'),
        )
        .order_by(WorkflowTemplate.is_featured.desc(), WorkflowTemplate.created_at.desc())
        .limit(10)
        .all()
    )

    knowledge_entries = (
        KnowledgeBaseEntry.query.filter_by(org_id=g.org.id, is_deleted=False)
        .order_by(KnowledgeBaseEntry.created_at.desc())
        .limit(25)
        .all()
    )

    field_errors: list[str] = []
    field_errors_payload = request.args.get("field_errors", "")
    if field_errors_payload:
        try:
            parsed = json.loads(field_errors_payload)
            if isinstance(parsed, list):
                field_errors = [str(item) for item in parsed]
        except json.JSONDecodeError:
            field_errors = []

    return render_template(
        "app/task_configure.html",
        task_config=config,
        task_type=task_type,
        projects=projects,
        integrations=integrations,
        matching_templates=matching_templates,
        knowledge_entries=knowledge_entries,
        category_colors=CATEGORY_COLORS,
        field_errors=field_errors,
    )


@tasks_bp.post("/tasks/run")
@login_required
@org_required
@quota_check
def run_task():
    """Create AutomationTask row, enqueue Celery worker, and redirect to monitor."""

    task_type = (request.form.get("task_type") or "").strip()
    if not validate_task_type(task_type):
        if _is_json_request():
            return error_response("Unknown task type.", status=400)
        flash("Unknown task type.", "danger")
        return redirect(url_for("tasks.task_launcher"))

    task_config = get_task_config(task_type)

    raw_input_data: dict[str, Any] = {}
    for field in task_config.get("input_fields", []):
        field_name = field.get("name")
        if not field_name:
            continue
        if field.get("type") == "checkboxes":
            raw_input_data[field_name] = request.form.getlist(field_name)
        else:
            raw_input_data[field_name] = request.form.get(field_name, "")

    is_valid, errors = validate_task_input_data(task_type, raw_input_data)
    if not is_valid:
        for message in errors:
            flash(message, "danger")
        return redirect(
            url_for(
                "tasks.task_configure",
                task_type=task_type,
                field_errors=json.dumps(errors),
            )
        )

    input_data = sanitize_task_inputs(raw_input_data)

    plan: Plan | None = db.session.get(Plan, g.org.plan_id) if g.org.plan_id else None
    plan_features = _normalize_features(plan.features_json if plan else [])
    has_api_access = "api_access" in plan_features or "all_features" in plan_features

    priority = "default"
    if plan and plan.slug == "enterprise":
        priority = "high"
    elif has_api_access:
        priority = "default"

    project_id = None
    project_id_raw = (request.form.get("project_id") or "").strip()
    if project_id_raw and validate_uuid(project_id_raw):
        project = Project.query.filter_by(
            id=UUID(project_id_raw),
            org_id=g.org.id,
            is_deleted=False,
            is_archived=False,
        ).first()
        if project is not None:
            project_id = project.id

    try:
        task = AutomationTask(
            org_id=g.org.id,
            project_id=project_id,
            user_id=current_user.id,
            task_type=task_type,
            task_name=task_config["name"],
            input_json=input_data,
            status="pending",
            priority=priority,
            timeout_seconds=task_config.get("timeout_seconds", 300),
        )
        db.session.add(task)
        db.session.commit()

        from app.tasks.agent_tasks import run_agent_task

        celery_result = run_agent_task.apply_async(
            args=[str(task.id)],
            queue="high" if priority == "high" else "default",
            countdown=0,
        )
        task.celery_task_id = celery_result.id

        audit_log = AuditLog(
            org_id=g.org.id,
            user_id=current_user.id,
            action="task.created",
            resource_type="task",
            resource_id=str(task.id),
            ip_address=request.remote_addr,
            user_agent=(request.user_agent.string[:500] if request.user_agent else None),
            extra_json={
                "task_type": task_type,
                "task_name": task_config["name"],
            },
        )
        db.session.add(audit_log)
        db.session.commit()
    except Exception:
        db.session.rollback()
        if _is_json_request():
            return error_response("Could not start task.", status=500)
        flash("Could not start task. Please try again.", "danger")
        return redirect(url_for("tasks.task_configure", task_type=task_type))

    flash("Task started! Your AI agent is working on it.", "info")
    return redirect(url_for("tasks.task_monitor", task_id=str(task.id)))


@tasks_bp.get("/tasks/<task_id>")
@login_required
@org_required
def task_detail(task_id: str):
    """Canonical task URL that routes to monitor or result by current status."""

    if not validate_uuid(task_id):
        abort(404)

    task = _task_lookup(task_id)
    if task is None:
        abort(404)

    if task.status == "done":
        return redirect(url_for("tasks.task_result", task_id=task_id))

    return redirect(url_for("tasks.task_monitor", task_id=task_id))


@tasks_bp.get("/tasks/<task_id>/monitor")
@login_required
@org_required
def task_monitor(task_id: str):
    """Task monitor page with step list and live log container."""

    if not validate_uuid(task_id):
        abort(404)

    task = _task_lookup(task_id)
    if task is None:
        abort(404)

    task_steps = (
        TaskStep.query.filter_by(task_id=task.id)
        .order_by(TaskStep.step_number.asc())
        .all()
    )

    task_config: dict[str, Any] = {}
    try:
        task_config = get_task_config(task.task_type)
    except KeyError:
        task_config = {
            "typical_steps": [],
            "name": task.task_name,
            "category_display": "Task",
        }

    return render_template(
        "app/task_monitor.html",
        task=task,
        steps=task_steps,
        task_config=task_config,
        task_id=str(task.id),
    )


@tasks_bp.get("/tasks/<task_id>/result")
@login_required
@org_required
def task_result(task_id: str):
    """Task output page for completed tasks."""

    if not validate_uuid(task_id):
        abort(404)

    task = _task_lookup(task_id)
    if task is None:
        abort(404)

    if task.status != "done":
        return redirect(url_for("tasks.task_monitor", task_id=task_id))

    output = TaskOutput.query.filter_by(task_id=task.id, is_deleted=False).first()
    steps = TaskStep.query.filter_by(task_id=task.id).order_by(TaskStep.step_number.asc()).all()

    if output and not output.content_text and output.file_path:
        try:
            output.content_text = file_service.read_output_file(output.file_path)
        except FileServiceError:
            output.content_text = ""

    output_json_pretty = ""
    if output and output.output_type == "json" and output.content_text:
        try:
            parsed = json.loads(output.content_text)
            output_json_pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            output_json_pretty = output.content_text

    table_rows: list[list[str]] = []
    table_headers: list[str] = []
    if output and output.output_type == "table" and output.content_text:
        reader = csv.reader(io.StringIO(output.content_text))
        parsed_rows = [row for row in reader if row]
        if parsed_rows:
            table_headers = parsed_rows[0]
            table_rows = parsed_rows[1:]

    projects = Project.query.filter_by(
        org_id=g.org.id,
        is_deleted=False,
        is_archived=False,
    ).order_by(Project.name.asc()).all()

    input_json = task.input_json if isinstance(task.input_json, dict) else {}
    notes = input_json.get("notes", []) if isinstance(input_json, dict) else []
    if not isinstance(notes, list):
        notes = []

    return render_template(
        "app/task_result.html",
        task=task,
        output=output,
        steps=steps,
        output_json_pretty=output_json_pretty,
        table_headers=table_headers,
        table_rows=table_rows,
        projects=projects,
        notes=notes,
    )


@tasks_bp.get("/tasks")
@login_required
@org_required
def task_history():
    """Task history listing with filters and pagination."""

    status_filter = (request.args.get("status") or "all").strip().lower()
    category_filter = (request.args.get("category") or "all").strip()
    search = (request.args.get("search") or "").strip()
    page = request.args.get("page", default=1, type=int)
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    export = (request.args.get("export") or "").strip().lower()

    query = AutomationTask.query.filter_by(org_id=g.org.id)

    if status_filter and status_filter != "all":
        query = query.filter(AutomationTask.status == status_filter)

    if category_filter and category_filter != "all":
        valid_task_types = [
            task_id
            for task_id, config in TASK_REGISTRY.items()
            if config.get("category") == category_filter
        ]
        if valid_task_types:
            query = query.filter(AutomationTask.task_type.in_(valid_task_types))
        else:
            query = query.filter(AutomationTask.id.is_(None))

    if search:
        search_like = f"%{search}%"
        query = query.filter(
            or_(
                AutomationTask.task_name.ilike(search_like),
                AutomationTask.task_type.ilike(search_like),
            )
        )

    if date_from:
        try:
            from_dt = datetime.strptime(date_from, "%Y-%m-%d")
            query = query.filter(AutomationTask.created_at >= from_dt)
        except ValueError:
            pass

    if date_to:
        try:
            to_dt = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(AutomationTask.created_at < to_dt)
        except ValueError:
            pass

    query = query.order_by(AutomationTask.created_at.desc())

    if export == "csv":
        all_tasks = query.all()
        buffer = export_service.tasks_to_csv(all_tasks)
        return send_file(
            buffer,
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"task_history_{datetime.utcnow().strftime('%Y%m%d')}.csv",
        )

    pagination = query.paginate(page=page, per_page=25, error_out=False)

    distinct_types = (
        db.session.query(AutomationTask.task_type)
        .filter(AutomationTask.org_id == g.org.id)
        .distinct()
        .all()
    )
    history_categories = sorted(
        {
            TASK_REGISTRY.get(row[0], {}).get("category", "uncategorized")
            for row in distinct_types
            if row and row[0]
        }
    )

    total_tasks = AutomationTask.query.filter_by(org_id=g.org.id).count()

    status_counts = {
        "all": total_tasks,
        "pending": AutomationTask.query.filter_by(org_id=g.org.id, status="pending").count(),
        "running": AutomationTask.query.filter_by(org_id=g.org.id, status="running").count(),
        "done": AutomationTask.query.filter_by(org_id=g.org.id, status="done").count(),
        "failed": AutomationTask.query.filter_by(org_id=g.org.id, status="failed").count(),
        "cancelled": AutomationTask.query.filter_by(org_id=g.org.id, status="cancelled").count(),
    }

    running_task_ids = [
        str(task.id)
        for task in pagination.items
        if task.status in ["running", "pending"]
    ]

    return render_template(
        "app/task_history.html",
        pagination=pagination,
        tasks=pagination,
        status_filter=status_filter,
        category_filter=category_filter,
        search=search,
        date_from=date_from,
        date_to=date_to,
        history_categories=history_categories,
        total_tasks=total_tasks,
        filtered_count=pagination.total,
        task_registry=TASK_REGISTRY,
        category_meta=CATEGORY_META,
        status_counts=status_counts,
        running_task_ids=running_task_ids,
    )


@tasks_bp.delete("/tasks/<task_id>")
@login_required
@org_required
def delete_task(task_id: str):
    """Delete task and related records."""

    if not validate_uuid(task_id):
        abort(404)

    task = _task_lookup(task_id)
    if task is None:
        abort(404)

    try:
        if task.status == "running" and task.celery_task_id:
            celery.control.revoke(task.celery_task_id, terminate=True)
            task.status = "cancelled"

        TaskOutput.query.filter_by(task_id=task.id).update(
            {
                "is_deleted": True,
                "deleted_at": datetime.utcnow(),
            }
        )

        TaskStep.query.filter_by(task_id=task.id).delete()
        db.session.delete(task)

        audit_log = AuditLog(
            org_id=g.org.id,
            user_id=current_user.id,
            action="task.deleted",
            resource_type="task",
            resource_id=task_id,
            ip_address=request.remote_addr,
            user_agent=(request.user_agent.string[:500] if request.user_agent else None),
        )
        db.session.add(audit_log)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return error_response("Could not delete task.", status=500)

    return success_response({"deleted": True})


@tasks_bp.get("/tasks/template/<template_id>/data")
@login_required
@org_required
def task_template_data(template_id: str):
    """Return template payload for task configure prefill actions."""

    if not validate_uuid(template_id):
        abort(404)

    template = WorkflowTemplate.query.filter_by(
        id=UUID(template_id),
        is_active=True,
    ).first()
    if template is None:
        abort(404)

    return success_response(
        {
            "id": str(template.id),
            "name": template.name,
            "description": template.description,
            "steps_json": template.steps_json,
        }
    )
