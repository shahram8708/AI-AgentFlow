"""Outputs blueprint routes."""

from __future__ import annotations

from datetime import datetime, timedelta
import io
import json
import os
import re
import zipfile
from typing import Any
from uuid import UUID

from flask import Blueprint, abort, current_app, g, render_template, request, send_file
from flask_login import current_user
from sqlalchemy import func

from app.extensions import db
from app.models import AuditLog, AutomationTask, Plan, Project, TaskOutput, TaskStep
from app.services.file_service import FileServiceError, file_service
from app.utils.decorators import login_required, org_required
from app.utils.response_helpers import error_response, success_response
from app.utils.validators import validate_uuid

outputs_bp = Blueprint("outputs", __name__)


def _parse_date(value: str, is_end: bool = False) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
        if is_end:
            return dt + timedelta(days=1)
        return dt
    except ValueError:
        return None


def _slugify_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower())
    cleaned = cleaned.strip("_")
    return cleaned[:60] or "output"


def _load_output_with_task_or_404(output_id: str) -> tuple[TaskOutput, AutomationTask]:
    if not validate_uuid(output_id):
        abort(404)

    output = TaskOutput.query.filter_by(id=UUID(output_id), is_deleted=False).first()
    if output is None:
        abort(404)

    task = AutomationTask.query.get(output.task_id)
    if task is None or str(task.org_id) != str(g.org.id):
        abort(404)

    return output, task


def _storage_limit_mb(plan: Plan | None) -> int:
    if plan is None:
        return 100

    features = plan.features_json if isinstance(plan.features_json, dict) else {}
    configured = features.get("storage_limit_mb") if isinstance(features, dict) else None
    if isinstance(configured, int) and configured > 0:
        return configured

    defaults = {
        "free": 100,
        "starter": 512,
        "pro": 2048,
        "team": 10240,
        "enterprise": 51200,
    }
    return defaults.get(plan.slug, 100)


def _output_size_expr():
    bind = db.session.get_bind() or db.engine
    if bind.dialect.name == "sqlite":
        text_size = func.length(func.coalesce(TaskOutput.content_text, ""))
    else:
        text_size = func.octet_length(func.coalesce(TaskOutput.content_text, ""))
    return func.coalesce(TaskOutput.file_size, text_size, 0)


def _create_audit_log(action: str, output: TaskOutput) -> None:
    user_agent = request.user_agent.string if request.user_agent else None
    db.session.add(
        AuditLog(
            org_id=g.org.id,
            user_id=current_user.id,
            action=action,
            resource_type="output",
            resource_id=str(output.id),
            ip_address=request.remote_addr,
            user_agent=user_agent[:500] if user_agent else None,
            extra_json={"output_type": output.output_type, "task_id": str(output.task_id)},
        )
    )


@outputs_bp.get("/outputs")
@login_required
@org_required
def outputs_home():
    """Render outputs library page with filtering and pagination."""

    output_type = str(request.args.get("output_type") or "all").strip().lower()
    task_type = str(request.args.get("task_type") or "all").strip()
    project_id = str(request.args.get("project_id") or "").strip()
    date_from = str(request.args.get("date_from") or "").strip()
    date_to = str(request.args.get("date_to") or "").strip()
    search = str(request.args.get("search") or "").strip()
    sort = str(request.args.get("sort") or "newest").strip().lower()
    page = max(request.args.get("page", default=1, type=int), 1)

    query = TaskOutput.query.join(AutomationTask).filter(
        AutomationTask.org_id == g.org.id,
        TaskOutput.is_deleted.is_(False),
    )

    if output_type and output_type != "all":
        query = query.filter(TaskOutput.output_type == output_type)

    if task_type and task_type != "all":
        query = query.filter(AutomationTask.task_type == task_type)

    if project_id and validate_uuid(project_id):
        query = query.filter(AutomationTask.project_id == UUID(project_id))

    from_dt = _parse_date(date_from)
    to_dt = _parse_date(date_to, is_end=True)
    if from_dt:
        query = query.filter(TaskOutput.created_at >= from_dt)
    if to_dt:
        query = query.filter(TaskOutput.created_at < to_dt)

    if search:
        pattern = f"%{search}%"
        query = query.filter(AutomationTask.task_name.ilike(pattern))

    if sort == "oldest":
        query = query.order_by(TaskOutput.created_at.asc())
    elif sort == "largest":
        query = query.order_by(_output_size_expr().desc(), TaskOutput.created_at.desc())
    elif sort == "task_name":
        query = query.order_by(AutomationTask.task_name.asc().nullslast(), TaskOutput.created_at.desc())
    else:
        query = query.order_by(TaskOutput.created_at.desc())

    pagination = query.paginate(page=page, per_page=20, error_out=False)

    total_storage_bytes = (
        db.session.query(func.coalesce(func.sum(_output_size_expr()), 0))
        .filter(
            TaskOutput.org_id == g.org.id,
            TaskOutput.is_deleted.is_(False),
        )
        .scalar()
        or 0
    )

    task_type_rows = (
        db.session.query(AutomationTask.task_type)
        .join(TaskOutput, TaskOutput.task_id == AutomationTask.id)
        .filter(
            AutomationTask.org_id == g.org.id,
            TaskOutput.is_deleted.is_(False),
        )
        .distinct()
        .order_by(AutomationTask.task_type.asc())
        .all()
    )
    task_types = [row[0] for row in task_type_rows if row and row[0]]

    projects = (
        Project.query.filter_by(org_id=g.org.id, is_deleted=False)
        .order_by(Project.name.asc())
        .all()
    )

    plan = db.session.get(Plan, g.org.plan_id) if g.org.plan_id else None
    retention_days = plan.output_retention_days if plan else current_app.config.get(
        "OUTPUT_RETENTION_DEFAULT_DAYS", 30
    )
    storage_used_mb = round(total_storage_bytes / (1024 * 1024), 2)

    return render_template(
        "app/outputs.html",
        outputs=pagination.items,
        pagination=pagination,
        total_count=pagination.total,
        storage_used_bytes=int(total_storage_bytes),
        storage_used_mb=storage_used_mb,
        plan_storage_limit_mb=_storage_limit_mb(plan),
        retention_days=retention_days,
        selected_output_type=output_type,
        selected_task_type=task_type,
        selected_project_id=project_id,
        selected_sort=sort,
        date_from=date_from,
        date_to=date_to,
        search=search,
        task_types=task_types,
        projects=projects,
    )


@outputs_bp.get("/outputs/<output_id>")
@login_required
@org_required
def output_detail(output_id: str):
    """Render output detail page."""

    output, task = _load_output_with_task_or_404(output_id)

    content = output.content_text or ""
    if not content and output.file_path:
        try:
            content = file_service.read_output_file(output.file_path)
        except FileServiceError:
            content = ""

    output_json_pretty = ""
    if output.output_type == "json" and content:
        try:
            output_json_pretty = json.dumps(json.loads(content), indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            output_json_pretty = content

    steps = (
        TaskStep.query.filter_by(task_id=task.id)
        .order_by(TaskStep.step_number.asc())
        .all()
    )

    plan = db.session.get(Plan, g.org.plan_id) if g.org.plan_id else None
    retention_days = plan.output_retention_days if plan and plan.output_retention_days > 0 else 30
    base_dt = output.created_at or task.completed_at or datetime.utcnow()
    expiry_date = base_dt + timedelta(days=retention_days)

    input_json = task.input_json if isinstance(task.input_json, dict) else {}
    notes = input_json.get("user_notes", []) if isinstance(input_json, dict) else []
    if not isinstance(notes, list):
        notes = []

    return render_template(
        "app/output_detail.html",
        output=output,
        task=task,
        output_content=content,
        output_json_pretty=output_json_pretty,
        steps=steps,
        notes=notes,
        expiry_date=expiry_date,
    )


@outputs_bp.delete("/outputs/<output_id>")
@login_required
@org_required
def delete_output(output_id: str):
    """Soft delete a task output."""

    output, _task = _load_output_with_task_or_404(output_id)

    if output.file_path:
        file_service.delete_file(output.file_path)

    output.is_deleted = True
    output.deleted_at = datetime.utcnow()
    _create_audit_log("output.deleted", output)
    db.session.commit()

    return success_response({"deleted": True})


@outputs_bp.get("/outputs/bulk-download")
@login_required
@org_required
def bulk_download_outputs():
    """Download selected outputs as a ZIP archive."""

    raw_ids = str(request.args.get("ids") or "").strip()
    ids = [item.strip() for item in raw_ids.split(",") if item.strip()]

    if not ids:
        return error_response("At least one output id is required", 400)
    if len(ids) > 50:
        return error_response("You can download up to 50 outputs at a time", 400)

    invalid_ids = [value for value in ids if not validate_uuid(value)]
    if invalid_ids:
        return error_response("Invalid output ID list", 400)

    output_uuids = [UUID(value) for value in ids]
    outputs = (
        TaskOutput.query.join(AutomationTask)
        .filter(
            TaskOutput.id.in_(output_uuids),
            TaskOutput.is_deleted.is_(False),
            AutomationTask.org_id == g.org.id,
        )
        .all()
    )

    if len(outputs) != len(output_uuids):
        return error_response("One or more outputs were not found", 404)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for output in outputs:
            task = AutomationTask.query.get(output.task_id)
            task_name = task.task_name if task and task.task_name else "output"
            task_slug = _slugify_filename(task_name)
            short_id = str(output.id)[:8]

            content_text = output.content_text or ""
            extension = "json" if output.output_type == "json" else "txt"
            content_name = f"{task_slug}_{short_id}.{extension}"

            if content_text:
                content_bytes = content_text.encode("utf-8")
                if len(content_bytes) > 5 * 1024 * 1024:
                    truncated = content_bytes[: 5 * 1024 * 1024].decode("utf-8", errors="ignore")
                    truncated += "\n\n[Truncated by AgentFlow for ZIP export at 5MB limit]"
                    zip_file.writestr(content_name, truncated)
                else:
                    zip_file.writestr(content_name, content_text)

            if output.file_path:
                try:
                    absolute_path = file_service.get_output_file_path(output.file_path)
                    if os.path.exists(absolute_path):
                        with open(absolute_path, "rb") as file_obj:
                            filename = output.file_name or os.path.basename(absolute_path)
                            zip_file.writestr(filename, file_obj.read())
                except FileServiceError:
                    continue

    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"agentflow_outputs_{datetime.utcnow().strftime('%Y%m%d')}.zip",
    )


@outputs_bp.post("/outputs/<output_id>/notes")
@login_required
@org_required
def save_output_note(output_id: str):
    """Save user notes associated with an output task."""

    output, task = _load_output_with_task_or_404(output_id)

    payload = request.get_json(silent=True) or {}
    note = str(payload.get("note") or "").strip()
    if not note:
        return error_response("note is required", 400)
    if len(note) > 2000:
        return error_response("note cannot exceed 2000 characters", 400)

    existing_input = dict(task.input_json or {})
    notes = list(existing_input.get("user_notes") or [])
    notes.append(
        {
            "note": note,
            "author": current_user.get_full_name(),
            "created_at": datetime.utcnow().isoformat(),
        }
    )
    existing_input["user_notes"] = notes
    task.input_json = existing_input
    db.session.commit()

    return success_response({"note_saved": True, "output_id": str(output.id)})


@outputs_bp.delete("/outputs/bulk")
@login_required
@org_required
def bulk_delete_outputs():
    """Soft delete multiple outputs for bulk actions."""

    payload = request.get_json(silent=True) or {}
    ids = payload.get("ids") if isinstance(payload, dict) else []
    if not isinstance(ids, list) or not ids:
        return error_response("ids must be a non empty array", 400)
    if len(ids) > 50:
        return error_response("You can delete up to 50 outputs at a time", 400)

    normalized_ids: list[UUID] = []
    for raw_id in ids:
        value = str(raw_id).strip()
        if not validate_uuid(value):
            return error_response("Invalid output ID list", 400)
        normalized_ids.append(UUID(value))

    outputs = (
        TaskOutput.query.join(AutomationTask)
        .filter(
            TaskOutput.id.in_(normalized_ids),
            TaskOutput.is_deleted.is_(False),
            AutomationTask.org_id == g.org.id,
        )
        .all()
    )

    deleted_count = 0
    for output in outputs:
        if output.file_path:
            file_service.delete_file(output.file_path)

        output.is_deleted = True
        output.deleted_at = datetime.utcnow()
        _create_audit_log("output.deleted", output)
        deleted_count += 1

    db.session.commit()
    return success_response({"deleted": True, "deleted_count": deleted_count})
