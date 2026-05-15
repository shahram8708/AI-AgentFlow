"""Projects blueprint routes."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any
from uuid import UUID

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_

from app.extensions import db
from app.models import (
    AuditLog,
    AutomationTask,
    KnowledgeBaseEntry,
    Project,
    TaskOutput,
    Workflow,
)
from app.utils.decorators import login_required, org_required
from app.utils.response_helpers import error_response, success_response
from app.utils.validators import validate_uuid

projects_bp = Blueprint("projects", __name__)

COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _is_json_request() -> bool:
    requested_with = (request.headers.get("X-Requested-With") or "").strip().lower()
    return (
        request.is_json
        or request.accept_mimetypes.best == "application/json"
        or requested_with == "xmlhttprequest"
    )


def _write_audit_log(
    action: str,
    project: Project,
    extra_json: dict[str, Any] | None = None,
) -> None:
    user_agent = request.user_agent.string if request.user_agent else None
    db.session.add(
        AuditLog(
            org_id=g.org.id,
            user_id=current_user.id,
            action=action,
            resource_type="project",
            resource_id=str(project.id),
            ip_address=request.remote_addr,
            user_agent=user_agent[:500] if user_agent else None,
            extra_json=extra_json,
        )
    )


def _load_project_or_404(project_id: str, include_deleted: bool = False) -> Project:
    if not validate_uuid(project_id):
        abort(404)

    query = Project.query.filter_by(id=UUID(project_id), org_id=g.org.id)
    if not include_deleted:
        query = query.filter_by(is_deleted=False)

    project = query.first()
    if project is None:
        abort(404)

    return project


def _normalize_project_payload(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip()
    color = str(payload.get("color") or "").strip()
    icon = str(payload.get("icon") or "").strip()

    if "name" in payload and (not name or len(name) > 255):
        return None, "name is required and must be at most 255 characters"

    if description and len(description) > 1000:
        return None, "description must be at most 1000 characters"

    if color and not COLOR_RE.match(color):
        return None, "color must be a valid hex code like #1A56DB"

    if icon and len(icon) > 50:
        return None, "icon must be at most 50 characters"

    normalized = {
        "name": name,
        "description": description,
        "color": color or "#1a56db",
        "icon": icon or "bi-folder",
    }
    return normalized, None

def _output_size_expr():
    bind = db.session.get_bind() or db.engine
    if bind.dialect.name == "sqlite":
        text_size = func.length(func.coalesce(TaskOutput.content_text, ""))
    else:
        text_size = func.octet_length(func.coalesce(TaskOutput.content_text, ""))
    return func.coalesce(TaskOutput.file_size, text_size, 0)


def _project_counts(project_ids: list[UUID]) -> dict[str, dict[str, int]]:
    if not project_ids:
        return {
            "tasks": {},
            "workflows": {},
            "outputs": {},
            "knowledge": {},
        }

    task_rows = (
        db.session.query(AutomationTask.project_id, func.count(AutomationTask.id))
        .filter(AutomationTask.project_id.in_(project_ids))
        .group_by(AutomationTask.project_id)
        .all()
    )
    workflow_rows = (
        db.session.query(Workflow.project_id, func.count(Workflow.id))
        .filter(
            Workflow.project_id.in_(project_ids),
            Workflow.is_deleted.is_(False),
        )
        .group_by(Workflow.project_id)
        .all()
    )
    output_rows = (
        db.session.query(AutomationTask.project_id, func.count(TaskOutput.id))
        .join(TaskOutput, TaskOutput.task_id == AutomationTask.id)
        .filter(
            AutomationTask.project_id.in_(project_ids),
            TaskOutput.is_deleted.is_(False),
        )
        .group_by(AutomationTask.project_id)
        .all()
    )
    knowledge_rows = (
        db.session.query(KnowledgeBaseEntry.project_id, func.count(KnowledgeBaseEntry.id))
        .filter(
            KnowledgeBaseEntry.project_id.in_(project_ids),
            KnowledgeBaseEntry.is_deleted.is_(False),
        )
        .group_by(KnowledgeBaseEntry.project_id)
        .all()
    )

    return {
        "tasks": {str(project_id): int(count) for project_id, count in task_rows},
        "workflows": {str(project_id): int(count) for project_id, count in workflow_rows},
        "outputs": {str(project_id): int(count) for project_id, count in output_rows},
        "knowledge": {str(project_id): int(count) for project_id, count in knowledge_rows},
    }


def _last_activity_for_project(project: Project) -> datetime | None:
    task_activity = (
        db.session.query(func.max(AutomationTask.updated_at))
        .filter(AutomationTask.project_id == project.id)
        .scalar()
    )
    workflow_activity = (
        db.session.query(func.max(Workflow.updated_at))
        .filter(Workflow.project_id == project.id, Workflow.is_deleted.is_(False))
        .scalar()
    )
    output_activity = (
        db.session.query(func.max(TaskOutput.created_at))
        .join(AutomationTask, TaskOutput.task_id == AutomationTask.id)
        .filter(
            AutomationTask.project_id == project.id,
            TaskOutput.is_deleted.is_(False),
        )
        .scalar()
    )
    knowledge_activity = (
        db.session.query(func.max(KnowledgeBaseEntry.updated_at))
        .filter(
            KnowledgeBaseEntry.project_id == project.id,
            KnowledgeBaseEntry.is_deleted.is_(False),
        )
        .scalar()
    )

    values = [value for value in [task_activity, workflow_activity, output_activity, knowledge_activity] if value]
    if not values:
        return project.updated_at or project.created_at
    return max(values)


@projects_bp.get("/projects")
@login_required
@org_required
def projects_home():
    """Render project manager page with counts."""

    status = str(request.args.get("status") or "all").strip().lower()
    search = str(request.args.get("search") or "").strip()

    query = Project.query.filter_by(org_id=g.org.id, is_deleted=False)

    if status == "active":
        query = query.filter(Project.is_archived.is_(False))
    elif status == "archived":
        query = query.filter(Project.is_archived.is_(True))

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            or_(
                Project.name.ilike(pattern),
                Project.description.ilike(pattern),
            )
        )

    projects = query.order_by(Project.is_archived.asc(), Project.updated_at.desc()).all()
    project_ids = [project.id for project in projects]
    counts = _project_counts(project_ids)

    active_projects = [project for project in projects if not project.is_archived]
    archived_projects = [project for project in projects if project.is_archived]

    return render_template(
        "app/projects.html",
        projects=projects,
        active_projects=active_projects,
        archived_projects=archived_projects,
        counts=counts,
        status=status,
        search=search,
    )


@projects_bp.post("/projects")
@login_required
@org_required
def create_project():
    """Create a new project."""

    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict(flat=True)
    payload = payload if isinstance(payload, dict) else {}

    normalized, error = _normalize_project_payload(payload)
    if error:
        return error_response(error, 400)

    if not normalized["name"]:
        return error_response("name is required", 400)

    project = Project(
        org_id=g.org.id,
        created_by=current_user.id,
        name=normalized["name"],
        description=normalized["description"] or None,
        color=normalized["color"],
        icon=normalized["icon"],
    )

    db.session.add(project)
    db.session.flush()
    _write_audit_log("project.created", project, {"name": project.name})
    db.session.commit()

    if _is_json_request():
        return success_response(
            {
                "project_id": str(project.id),
                "redirect": f"/projects/{project.id}",
                "name": project.name,
                "color": project.color,
                "icon": project.icon,
            }
        )

    flash("Project created successfully", "success")
    return redirect(url_for("projects.projects_home"))


@projects_bp.get("/projects/<project_id>")
@login_required
@org_required
def project_detail(project_id: str):
    """Render project detail with tabbed content."""

    project = _load_project_or_404(project_id)
    tab = str(request.args.get("tab") or "tasks").strip().lower()
    if tab not in {"tasks", "workflows", "outputs", "knowledge", "settings"}:
        tab = "tasks"

    tasks = (
        AutomationTask.query.filter_by(project_id=project.id)
        .order_by(AutomationTask.created_at.desc())
        .limit(25)
        .all()
    )
    workflows = Workflow.query.filter_by(project_id=project.id, is_deleted=False).all()
    outputs = (
        TaskOutput.query.join(AutomationTask)
        .filter(
            AutomationTask.project_id == project.id,
            TaskOutput.is_deleted.is_(False),
        )
        .order_by(TaskOutput.created_at.desc())
        .limit(20)
        .all()
    )
    knowledge_entries = KnowledgeBaseEntry.query.filter_by(
        project_id=project.id,
        is_deleted=False,
    ).all()

    total_tasks = AutomationTask.query.filter_by(project_id=project.id).count()
    total_workflows = Workflow.query.filter_by(project_id=project.id, is_deleted=False).count()
    total_outputs = (
        TaskOutput.query.join(AutomationTask)
        .filter(
            AutomationTask.project_id == project.id,
            TaskOutput.is_deleted.is_(False),
        )
        .count()
    )
    total_knowledge = KnowledgeBaseEntry.query.filter_by(
        project_id=project.id,
        is_deleted=False,
    ).count()

    completed_today = (
        AutomationTask.query.filter(
            AutomationTask.project_id == project.id,
            AutomationTask.status == "done",
            AutomationTask.completed_at >= datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0),
        ).count()
    )

    output_storage_bytes = (
        db.session.query(func.coalesce(func.sum(_output_size_expr()), 0))
        .join(AutomationTask, TaskOutput.task_id == AutomationTask.id)
        .filter(
            AutomationTask.project_id == project.id,
            TaskOutput.is_deleted.is_(False),
        )
        .scalar()
        or 0
    )

    stats = {
        "tasks": int(total_tasks),
        "workflows": int(total_workflows),
        "outputs": int(total_outputs),
        "knowledge": int(total_knowledge),
        "completed_today": int(completed_today),
        "output_storage_bytes": int(output_storage_bytes),
        "last_activity": _last_activity_for_project(project),
    }

    return render_template(
        "app/project_detail.html",
        project=project,
        tab=tab,
        tasks=tasks,
        workflows=workflows,
        outputs=outputs,
        knowledge_entries=knowledge_entries,
        stats=stats,
    )


@projects_bp.put("/projects/<project_id>")
@login_required
@org_required
def update_project(project_id: str):
    """Update project metadata fields."""

    project = _load_project_or_404(project_id)
    payload = request.get_json(silent=True) or {}

    allowed_payload: dict[str, Any] = {}
    for field in ["name", "description", "color", "icon"]:
        if field in payload:
            allowed_payload[field] = payload.get(field)

    if not allowed_payload:
        return error_response("No updatable fields provided", 400)

    candidate_payload = {
        "name": allowed_payload.get("name", project.name),
        "description": allowed_payload.get("description", project.description or ""),
        "color": allowed_payload.get("color", project.color or "#1a56db"),
        "icon": allowed_payload.get("icon", project.icon or "bi-folder"),
    }
    normalized, error = _normalize_project_payload(candidate_payload)
    if error:
        return error_response(error, 400)

    project.name = normalized["name"]
    project.description = normalized["description"] or None
    project.color = normalized["color"]
    project.icon = normalized["icon"]

    _write_audit_log("project.updated", project)
    db.session.commit()

    return success_response({"updated": True})


@projects_bp.post("/projects/<project_id>/archive")
@login_required
@org_required
def archive_project(project_id: str):
    """Toggle project archive state."""

    project = _load_project_or_404(project_id)
    project.is_archived = not project.is_archived

    action = "project.archived" if project.is_archived else "project.unarchived"
    _write_audit_log(action, project)
    db.session.commit()

    return success_response({"is_archived": project.is_archived})


@projects_bp.delete("/projects/<project_id>")
@login_required
@org_required
def delete_project(project_id: str):
    """Soft delete project after dependency checks."""

    project = _load_project_or_404(project_id)

    task_count = AutomationTask.query.filter_by(project_id=project.id).count()
    workflow_count = Workflow.query.filter_by(project_id=project.id, is_deleted=False).count()

    if task_count > 0 or workflow_count > 0:
        return error_response(
            "Cannot delete project with existing tasks or workflows. Archive it instead or delete its tasks first.",
            400,
        )

    project.is_deleted = True
    project.deleted_at = datetime.utcnow()

    knowledge_entries = KnowledgeBaseEntry.query.filter_by(
        project_id=project.id,
        is_deleted=False,
    ).all()
    for entry in knowledge_entries:
        entry.is_deleted = True
        entry.deleted_at = datetime.utcnow()

    _write_audit_log("project.deleted", project)
    db.session.commit()

    return success_response({"deleted": True})


@projects_bp.post("/projects/<project_id>/duplicate")
@login_required
@org_required
def duplicate_project(project_id: str):
    """Duplicate project container without related artifacts."""

    project = _load_project_or_404(project_id)

    new_project = Project(
        org_id=g.org.id,
        created_by=current_user.id,
        name=f"Copy of {project.name}"[:255],
        description=project.description,
        color=project.color,
        icon=project.icon,
    )

    db.session.add(new_project)
    db.session.flush()
    _write_audit_log(
        "project.duplicated",
        new_project,
        extra_json={"source_project_id": str(project.id)},
    )
    db.session.commit()

    return success_response({"project_id": str(new_project.id)})
