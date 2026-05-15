"""Workflows blueprint routes."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any
from uuid import UUID

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import and_, case, func, or_

from app.extensions import db
from app.forms.workflow import WorkflowForm, WorkflowRunForm
from app.models import (
    AuditLog,
    AutomationTask,
    Project,
    ScheduledJob,
    UsageRecord,
    Workflow,
    WorkflowTemplate,
)
from app.routes.schedules import get_cron_description
from app.services.agent_runner import TASK_REGISTRY, get_all_categories, get_task_config
from app.utils.decorators import login_required, org_required, quota_check
from app.utils.response_helpers import error_response, success_response
from app.utils.validators import validate_uuid

workflows_bp = Blueprint("workflows", __name__)

ALLOWED_TRIGGER_TYPES = {"manual", "scheduled", "webhook"}
ALLOWED_PRIORITY_TYPES = {"default", "high", "low"}


def _is_json_request() -> bool:
    """Return True when route should respond with JSON payloads."""

    requested_with = (request.headers.get("X-Requested-With") or "").strip().lower()
    return (
        request.path.startswith("/api")
        or request.is_json
        or request.accept_mimetypes.best == "application/json"
        or requested_with == "xmlhttprequest"
    )


def _as_bool(value: Any) -> bool:
    """Convert common truthy and falsy values into booleans."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_tags(raw_tags: Any) -> list[str]:
    """Normalize comma separated tags to de-duplicated list."""

    if raw_tags is None:
        return []

    if isinstance(raw_tags, list):
        source_values = [str(item) for item in raw_tags]
    else:
        source_values = str(raw_tags).split(",")

    normalized: list[str] = []
    seen: set[str] = set()
    for tag in source_values:
        cleaned = " ".join(tag.strip().split())
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(cleaned[:50])

    return normalized[:20]


def _workflow_request_payload() -> dict[str, Any]:
    """Extract workflow payload from JSON or form submissions."""

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        return payload if isinstance(payload, dict) else {}

    payload = request.form.to_dict(flat=True)
    if "steps_json" in request.form:
        payload["steps_json"] = request.form.get("steps_json")
    return payload


def _project_choices() -> list[tuple[str, str]]:
    """Return project choices for workflow forms."""

    projects = (
        Project.query.filter_by(org_id=g.org.id, is_deleted=False, is_archived=False)
        .order_by(Project.name.asc())
        .all()
    )
    choices: list[tuple[str, str]] = [("", "All Projects")]
    for project in projects:
        choices.append((str(project.id), project.name))
    return choices


def _workflow_project_choices() -> list[tuple[str, str]]:
    """Return workflow-specific project choices."""

    choices = _project_choices()
    if choices:
        choices[0] = ("", "No project")
    return choices


def _set_workflow_form_choices(form: WorkflowForm) -> None:
    """Attach org scoped project choices to workflow form."""

    form.project_id.choices = _workflow_project_choices()


def _set_workflow_run_form_choices(form: WorkflowRunForm) -> None:
    """Attach org scoped project choices to workflow run form."""

    form.project_id.choices = _workflow_project_choices()


def _get_workflow_or_404(wf_id: str) -> Workflow:
    """Resolve a workflow UUID scoped to current organization."""

    if not validate_uuid(wf_id):
        abort(404)

    workflow = Workflow.query.filter_by(
        id=UUID(wf_id),
        org_id=g.org.id,
        is_deleted=False,
    ).first()
    if workflow is None:
        abort(404)
    return workflow


def _resolve_project_id(project_id_raw: Any) -> tuple[UUID | None, str | None]:
    """Validate project id and ensure it belongs to current organization."""

    if project_id_raw in (None, "", "null"):
        return None, None

    project_id_str = str(project_id_raw).strip()
    if not validate_uuid(project_id_str):
        return None, "Invalid project selected."

    project = Project.query.filter_by(
        id=UUID(project_id_str),
        org_id=g.org.id,
        is_deleted=False,
        is_archived=False,
    ).first()
    if project is None:
        return None, "Selected project does not belong to your organization."

    return project.id, None


def _normalize_step(step: dict[str, Any]) -> dict[str, Any] | None:
    """Validate and normalize workflow step data to a canonical shape."""

    task_type = str(step.get("task_type", "")).strip()
    if not task_type:
        return None

    task_config = TASK_REGISTRY.get(task_type)
    if not task_config:
        return None

    input_json = step.get("input_json")
    if input_json is None and isinstance(step.get("input"), dict):
        input_json = step.get("input")

    if input_json is None:
        input_json = {}

    if not isinstance(input_json, dict):
        return None

    task_name = step.get("task_name") or step.get("step_name") or task_config.get("name")
    normalized: dict[str, Any] = {
        "task_type": task_type,
        "task_name": str(task_name or task_type),
        "input_json": input_json,
        "description": str(step.get("description") or task_config.get("description") or ""),
        "category": str(step.get("category") or task_config.get("category") or ""),
    }

    return normalized


def _validate_steps_payload(raw_steps: Any) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Parse and validate workflow steps payload."""

    parsed_steps: Any = raw_steps
    if isinstance(parsed_steps, str):
        payload = parsed_steps.strip()
        if not payload:
            parsed_steps = []
        else:
            try:
                parsed_steps = json.loads(payload)
            except json.JSONDecodeError:
                return None, "steps_json must be valid JSON."

    if parsed_steps is None:
        parsed_steps = []

    if not isinstance(parsed_steps, list):
        return None, "steps_json must be an array of step objects."

    normalized_steps: list[dict[str, Any]] = []
    invalid_task_types: set[str] = set()

    for index, step in enumerate(parsed_steps):
        if not isinstance(step, dict):
            return None, f"Step {index + 1} must be an object."

        task_type = str(step.get("task_type", "")).strip()
        if not task_type:
            return None, f"Step {index + 1} is missing required field 'task_type'."

        if task_type not in TASK_REGISTRY:
            invalid_task_types.add(task_type)
            continue

        if "task_name" not in step and "step_name" not in step:
            return None, f"Step {index + 1} is missing required field 'task_name'."

        if "input_json" not in step and "input" not in step:
            return None, f"Step {index + 1} is missing required field 'input_json'."

        if "input_json" in step and not isinstance(step.get("input_json"), dict):
            return None, f"Step {index + 1} field 'input_json' must be an object."

        normalized = _normalize_step(step)
        if normalized is None:
            return None, (
                f"Step {index + 1} must include task_type, task_name, and input_json object."
            )

        normalized_steps.append(normalized)

    if invalid_task_types:
        invalid_list = ", ".join(sorted(invalid_task_types))
        return None, f"Invalid task_type values: {invalid_list}"

    return normalized_steps, None


def _create_audit_log(
    action: str,
    resource_type: str,
    resource_id: str,
    before_json: dict[str, Any] | None = None,
    after_json: dict[str, Any] | None = None,
    extra_json: dict[str, Any] | None = None,
) -> None:
    """Append an audit log row for workflow actions."""

    db.session.add(
        AuditLog(
            org_id=g.org.id,
            user_id=current_user.id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=request.remote_addr,
            user_agent=(request.user_agent.string[:500] if request.user_agent else None),
            before_json=before_json,
            after_json=after_json,
            extra_json=extra_json,
        )
    )


def _estimate_workflow_seconds(steps: list[dict[str, Any]]) -> int:
    """Estimate workflow runtime from task registry metadata."""

    total = 0
    for step in steps:
        task_type = str(step.get("task_type", ""))
        config = TASK_REGISTRY.get(task_type, {})
        total += int(config.get("estimated_seconds", 60) or 60)
    return total


def _detect_workflow_difficulty(steps: list[dict[str, Any]]) -> str:
    """Derive a rough difficulty level from workflow steps."""

    order = {"beginner": 1, "intermediate": 2, "advanced": 3}
    highest = "beginner"
    for step in steps:
        task_type = str(step.get("task_type", ""))
        config = TASK_REGISTRY.get(task_type, {})
        difficulty = str(config.get("difficulty", "beginner")).lower()
        if order.get(difficulty, 1) > order.get(highest, 1):
            highest = difficulty
    return highest


def _build_template_from_workflow(
    workflow: Workflow,
    steps: list[dict[str, Any]],
    payload: dict[str, Any],
) -> WorkflowTemplate:
    """Create a WorkflowTemplate record from a workflow definition."""

    first_task_type = steps[0].get("task_type") if steps else ""
    first_config = TASK_REGISTRY.get(str(first_task_type), {})
    template_name = str(payload.get("template_name") or workflow.name or "Workflow Template").strip()
    template_category = str(
        payload.get("template_category")
        or first_config.get("category")
        or "custom"
    ).strip()
    template_difficulty = str(
        payload.get("template_difficulty") or _detect_workflow_difficulty(steps)
    ).strip().lower()

    required_integrations_raw = payload.get("required_integrations", [])
    required_integrations: list[str]
    if isinstance(required_integrations_raw, list):
        required_integrations = [str(item).strip() for item in required_integrations_raw if str(item).strip()]
    elif isinstance(required_integrations_raw, str):
        required_integrations = [item.strip() for item in required_integrations_raw.split(",") if item.strip()]
    else:
        required_integrations = []

    return WorkflowTemplate(
        name=template_name[:255],
        category=template_category[:100] or "custom",
        description=workflow.description,
        steps_json=steps,
        required_integrations=required_integrations,
        usage_count=0,
        is_featured=False,
        is_active=True,
        estimated_time_seconds=_estimate_workflow_seconds(steps),
        difficulty=template_difficulty if template_difficulty in {"beginner", "intermediate", "advanced"} else "beginner",
        tags_json=workflow.tags_json if isinstance(workflow.tags_json, list) else [],
    )


def _enrich_workflow_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach task registry metadata for UI rendering."""

    enriched: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        task_type = str(step.get("task_type", ""))
        task_config = TASK_REGISTRY.get(task_type, {})
        enriched.append(
            {
                "index": index,
                "task_type": task_type,
                "task_name": step.get("task_name") or task_config.get("name") or task_type,
                "description": step.get("description") or task_config.get("description") or "",
                "category": step.get("category") or task_config.get("category_display") or task_config.get("category") or "General",
                "input_json": step.get("input_json") if isinstance(step.get("input_json"), dict) else {},
                "task_config": task_config,
            }
        )
    return enriched


@workflows_bp.get("/workflows")
@login_required
@org_required
def workflows_home():
    """Render workflows library with filters and summary stats."""

    trigger_type_filter = str(request.args.get("trigger_type") or "").strip().lower()
    project_filter = str(request.args.get("project_id") or "").strip()
    search_query = str(request.args.get("search") or "").strip()
    sort_key = str(request.args.get("sort") or "newest").strip().lower()
    page = max(request.args.get("page", default=1, type=int), 1)

    query = Workflow.query.filter_by(org_id=g.org.id, is_deleted=False)

    if trigger_type_filter and trigger_type_filter in ALLOWED_TRIGGER_TYPES:
        query = query.filter(Workflow.trigger_type == trigger_type_filter)

    if project_filter and validate_uuid(project_filter):
        query = query.filter(Workflow.project_id == UUID(project_filter))

    if search_query:
        pattern = f"%{search_query}%"
        query = query.filter(
            or_(
                Workflow.name.ilike(pattern),
                Workflow.description.ilike(pattern),
            )
        )

    if sort_key == "last_run":
        query = query.order_by(Workflow.last_run_at.desc().nullslast(), Workflow.updated_at.desc())
    elif sort_key == "most_used":
        query = query.order_by(Workflow.run_count.desc(), Workflow.updated_at.desc())
    elif sort_key == "alphabetical":
        query = query.order_by(Workflow.name.asc())
    else:
        query = query.order_by(Workflow.updated_at.desc())

    pagination = query.paginate(page=page, per_page=12, error_out=False)
    workflows = pagination.items
    workflow_ids = [workflow.id for workflow in workflows]

    workflow_run_counts: dict[str, int] = {}
    workflow_last_runs: dict[str, dict[str, Any]] = {}
    active_scheduled_workflow_ids: set[str] = set()

    if workflow_ids:
        count_rows = (
            db.session.query(
                AutomationTask.workflow_id,
                func.count(AutomationTask.id),
            )
            .filter(AutomationTask.workflow_id.in_(workflow_ids))
            .group_by(AutomationTask.workflow_id)
            .all()
        )
        workflow_run_counts = {str(wf_id): int(total) for wf_id, total in count_rows}

        ranked_runs = (
            db.session.query(
                AutomationTask.workflow_id.label("workflow_id"),
                AutomationTask.status.label("status"),
                AutomationTask.created_at.label("created_at"),
                func.row_number()
                .over(
                    partition_by=AutomationTask.workflow_id,
                    order_by=AutomationTask.created_at.desc(),
                )
                .label("row_num"),
            )
            .filter(AutomationTask.workflow_id.in_(workflow_ids))
            .subquery()
        )

        last_rows = (
            db.session.query(
                ranked_runs.c.workflow_id,
                ranked_runs.c.status,
                ranked_runs.c.created_at,
            )
            .filter(ranked_runs.c.row_num == 1)
            .all()
        )

        workflow_last_runs = {
            str(row.workflow_id): {"status": row.status, "created_at": row.created_at}
            for row in last_rows
        }

        active_schedule_rows = (
            db.session.query(ScheduledJob.workflow_id)
            .filter(
                ScheduledJob.workflow_id.in_(workflow_ids),
                ScheduledJob.org_id == g.org.id,
                ScheduledJob.is_active.is_(True),
            )
            .distinct()
            .all()
        )
        active_scheduled_workflow_ids = {str(row.workflow_id) for row in active_schedule_rows}

    projects = (
        Project.query.filter_by(org_id=g.org.id, is_deleted=False, is_archived=False)
        .order_by(Project.name.asc())
        .all()
    )

    run_form = WorkflowRunForm()
    _set_workflow_run_form_choices(run_form)

    return render_template(
        "app/workflows.html",
        workflow_pagination=pagination,
        workflows=workflows,
        workflow_run_counts=workflow_run_counts,
        workflow_last_runs=workflow_last_runs,
        active_scheduled_workflow_ids=active_scheduled_workflow_ids,
        projects=projects,
        filter_trigger_type=trigger_type_filter,
        filter_project_id=project_filter,
        filter_search=search_query,
        filter_sort=sort_key,
        run_form=run_form,
    )


@workflows_bp.get("/workflows/new")
@login_required
@org_required
def workflow_builder_new():
    """Render workflow builder for new workflows."""

    form = WorkflowForm()
    _set_workflow_form_choices(form)

    return render_template(
        "app/workflow_builder.html",
        form=form,
        workflow=None,
        task_registry=TASK_REGISTRY,
        task_registry_json=json.dumps(TASK_REGISTRY),
        categories=get_all_categories(),
    )


@workflows_bp.post("/workflows")
@login_required
@org_required
def create_workflow():
    """Create a new workflow from JSON or form data."""

    payload = _workflow_request_payload()

    name = str(payload.get("name") or "").strip()
    if not name:
        if _is_json_request():
            return error_response("Workflow name is required.", status=400)
        flash("Workflow name is required.", "danger")
        return redirect(url_for("workflows.workflow_builder_new"))

    steps, steps_error = _validate_steps_payload(payload.get("steps_json"))
    if steps_error:
        if _is_json_request():
            return error_response(steps_error, status=400)
        flash(steps_error, "danger")
        return redirect(url_for("workflows.workflow_builder_new"))

    project_id, project_error = _resolve_project_id(payload.get("project_id"))
    if project_error:
        if _is_json_request():
            return error_response(project_error, status=400)
        flash(project_error, "danger")
        return redirect(url_for("workflows.workflow_builder_new"))

    trigger_type = str(payload.get("trigger_type") or "manual").strip().lower()
    if trigger_type not in ALLOWED_TRIGGER_TYPES:
        trigger_type = "manual"

    workflow = Workflow(
        org_id=g.org.id,
        created_by=current_user.id,
        name=name[:255],
        description=str(payload.get("description") or "").strip()[:1000] or None,
        trigger_type=trigger_type,
        project_id=project_id,
        is_public=_as_bool(payload.get("is_public")),
        steps_json=steps or [],
        tags_json=_parse_tags(payload.get("tags")),
        version=1,
    )

    save_as_template = _as_bool(payload.get("save_as_template"))

    try:
        db.session.add(workflow)
        db.session.flush()

        if save_as_template:
            db.session.add(_build_template_from_workflow(workflow, steps or [], payload))

        _create_audit_log(
            action="workflow.created",
            resource_type="workflow",
            resource_id=str(workflow.id),
            after_json={"name": workflow.name, "step_count": len(steps or [])},
            extra_json={"save_as_template": save_as_template},
        )

        db.session.commit()
    except Exception:
        db.session.rollback()
        if _is_json_request():
            return error_response("Could not create workflow.", status=500)
        flash("Could not create workflow. Please try again.", "danger")
        return redirect(url_for("workflows.workflow_builder_new"))

    redirect_url = f"/workflows/{workflow.id}?mode=edit"
    if _is_json_request():
        return success_response({"workflow_id": str(workflow.id), "redirect": redirect_url})

    flash("Workflow created successfully.", "success")
    return redirect(f"/workflows/{workflow.id}")


@workflows_bp.get("/workflows/<wf_id>")
@login_required
@org_required
def workflow_detail_or_edit(wf_id: str):
    """Render workflow detail page or edit builder based on mode query parameter."""

    workflow = _get_workflow_or_404(wf_id)
    mode = str(request.args.get("mode") or "detail").strip().lower()

    if mode == "edit":
        form = WorkflowForm(obj=workflow)
        _set_workflow_form_choices(form)
        form.project_id.data = str(workflow.project_id) if workflow.project_id else ""
        form.tags.data = ", ".join(workflow.tags_json) if isinstance(workflow.tags_json, list) else ""

        return render_template(
            "app/workflow_builder.html",
            form=form,
            workflow=workflow,
            task_registry=TASK_REGISTRY,
            task_registry_json=json.dumps(TASK_REGISTRY),
            categories=get_all_categories(),
        )

    run_history = (
        AutomationTask.query.filter_by(workflow_id=workflow.id)
        .order_by(AutomationTask.created_at.desc())
        .limit(20)
        .all()
    )

    projects = (
        Project.query.filter_by(org_id=g.org.id, is_deleted=False, is_archived=False)
        .order_by(Project.name.asc())
        .all()
    )

    stats_row = (
        db.session.query(
            func.count(AutomationTask.id).label("total_count"),
            func.coalesce(
                func.sum(case((AutomationTask.status == "done", 1), else_=0)),
                0,
            ).label("success_count"),
            func.coalesce(
                func.sum(case((AutomationTask.status == "failed", 1), else_=0)),
                0,
            ).label("failed_count"),
            func.avg(
                case(
                    (
                        and_(
                            AutomationTask.started_at.isnot(None),
                            AutomationTask.completed_at.isnot(None),
                        ),
                        func.extract("epoch", AutomationTask.completed_at - AutomationTask.started_at),
                    ),
                    else_=None,
                )
            ).label("avg_duration_seconds"),
        )
        .filter(AutomationTask.workflow_id == workflow.id)
        .one()
    )

    success_count = int(stats_row.success_count or 0)
    failed_count = int(stats_row.failed_count or 0)
    total_count = int(stats_row.total_count or 0)
    avg_duration_seconds = float(stats_row.avg_duration_seconds or 0.0)

    trigger_rows = (
        db.session.query(
            case(
                (AutomationTask.task_name.ilike("Scheduled:%"), "scheduled"),
                else_="manual",
            ).label("triggered_by"),
            func.count(AutomationTask.id).label("total"),
        )
        .filter(AutomationTask.workflow_id == workflow.id)
        .group_by("triggered_by")
        .all()
    )
    trigger_summary = {str(row.triggered_by): int(row.total or 0) for row in trigger_rows}
    most_frequent_trigger = "Scheduled" if trigger_summary.get("scheduled", 0) > trigger_summary.get("manual", 0) else "Manual"

    active_schedule = (
        ScheduledJob.query.filter_by(
            org_id=g.org.id,
            workflow_id=workflow.id,
            is_active=True,
        )
        .order_by(ScheduledJob.created_at.desc())
        .first()
    )
    active_schedule_description = (
        get_cron_description(active_schedule.cron_expression)
        if active_schedule is not None
        else None
    )

    run_form = WorkflowRunForm()
    _set_workflow_run_form_choices(run_form)

    return render_template(
        "app/workflow_detail.html",
        workflow=workflow,
        run_history=run_history,
        projects=projects,
        steps_enriched=_enrich_workflow_steps(workflow.steps_json if isinstance(workflow.steps_json, list) else []),
        total_count=total_count,
        success_count=success_count,
        failed_count=failed_count,
        avg_duration_seconds=avg_duration_seconds,
        success_rate=(round((success_count / total_count) * 100, 1) if total_count else 0.0),
        trigger_summary=trigger_summary,
        most_frequent_trigger=most_frequent_trigger,
        active_schedule=active_schedule,
        active_schedule_description=active_schedule_description,
        run_form=run_form,
    )


@workflows_bp.put("/workflows/<wf_id>")
@login_required
@org_required
def update_workflow(wf_id: str):
    """Update workflow definition and increment version."""

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return error_response("Invalid JSON payload.", status=400)

    workflow = _get_workflow_or_404(wf_id)

    name = str(payload.get("name") or "").strip()
    if not name:
        return error_response("Workflow name is required.", status=400)

    steps, steps_error = _validate_steps_payload(payload.get("steps_json"))
    if steps_error:
        return error_response(steps_error, status=400)

    project_id, project_error = _resolve_project_id(payload.get("project_id"))
    if project_error:
        return error_response(project_error, status=400)

    trigger_type = str(payload.get("trigger_type") or workflow.trigger_type or "manual").strip().lower()
    if trigger_type not in ALLOWED_TRIGGER_TYPES:
        trigger_type = "manual"

    old_version = int(workflow.version or 1)
    workflow.version = old_version + 1
    workflow.name = name[:255]
    workflow.description = str(payload.get("description") or "").strip()[:1000] or None
    workflow.trigger_type = trigger_type
    workflow.project_id = project_id
    workflow.is_public = _as_bool(payload.get("is_public"))
    workflow.steps_json = steps or []
    workflow.tags_json = _parse_tags(payload.get("tags"))
    workflow.updated_at = datetime.utcnow()

    save_as_template = _as_bool(payload.get("save_as_template"))

    try:
        if save_as_template:
            db.session.add(_build_template_from_workflow(workflow, steps or [], payload))

        _create_audit_log(
            action="workflow.updated",
            resource_type="workflow",
            resource_id=str(workflow.id),
            before_json={"version": old_version},
            after_json={"version": workflow.version},
            extra_json={"save_as_template": save_as_template},
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        return error_response("Could not update workflow.", status=500)

    return success_response({"workflow_id": str(workflow.id), "version": workflow.version})


@workflows_bp.delete("/workflows/<wf_id>")
@login_required
@org_required
def delete_workflow(wf_id: str):
    """Soft delete workflow after validating active schedule dependencies."""

    workflow = _get_workflow_or_404(wf_id)

    active_schedule_count = ScheduledJob.query.filter_by(
        workflow_id=workflow.id,
        is_active=True,
    ).count()
    if active_schedule_count > 0:
        return error_response(
            "Cannot delete workflow with active schedules. Please deactivate schedules first.",
            status=400,
        )

    workflow.is_deleted = True
    workflow.deleted_at = datetime.utcnow()

    ScheduledJob.query.filter_by(workflow_id=workflow.id).update(
        {"is_active": False},
        synchronize_session=False,
    )

    try:
        _create_audit_log(
            action="workflow.deleted",
            resource_type="workflow",
            resource_id=str(workflow.id),
            after_json={"is_deleted": True},
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        return error_response("Could not delete workflow.", status=500)

    return success_response({"deleted": True})


@workflows_bp.post("/workflows/<wf_id>/run")
@login_required
@org_required
@quota_check
def run_workflow(wf_id: str):
    """Run a workflow by enqueuing its first step as an AutomationTask."""

    workflow = _get_workflow_or_404(wf_id)
    steps = workflow.steps_json if isinstance(workflow.steps_json, list) else []
    if not steps:
        return error_response("Cannot run a workflow with no steps.", status=400)

    payload = _workflow_request_payload()

    parameter_overrides_raw = payload.get("parameter_overrides")
    parameter_overrides: dict[str, Any] = {}
    if isinstance(parameter_overrides_raw, dict):
        parameter_overrides = parameter_overrides_raw
    elif isinstance(parameter_overrides_raw, str) and parameter_overrides_raw.strip():
        try:
            parsed_overrides = json.loads(parameter_overrides_raw)
        except json.JSONDecodeError:
            return error_response("Override parameters must be valid JSON.", status=400)
        if not isinstance(parsed_overrides, dict):
            return error_response("Override parameters must be a JSON object.", status=400)
        parameter_overrides = parsed_overrides

    run_project_id, project_error = _resolve_project_id(payload.get("project_id"))
    if project_error:
        return error_response(project_error, status=400)

    if run_project_id is None:
        run_project_id = workflow.project_id

    priority = str(payload.get("priority") or "default").strip().lower()
    if priority not in ALLOWED_PRIORITY_TYPES:
        priority = "default"

    first_step = steps[0] if isinstance(steps[0], dict) else {}
    task_type = str(first_step.get("task_type") or "").strip()
    if not task_type:
        return error_response("First workflow step is invalid.", status=400)

    if task_type not in TASK_REGISTRY:
        return error_response(f"Invalid task_type in workflow: {task_type}", status=400)

    try:
        task_config = get_task_config(task_type)
    except KeyError:
        return error_response(f"Unknown task type: {task_type}", status=400)

    base_inputs = first_step.get("input_json") if isinstance(first_step.get("input_json"), dict) else {}
    merged_inputs = {**base_inputs, **parameter_overrides}

    queue_name = priority if priority in {"high", "low"} else "default"

    try:
        task = AutomationTask(
            org_id=g.org.id,
            project_id=run_project_id,
            workflow_id=workflow.id,
            user_id=current_user.id,
            task_type=task_type,
            task_name=f"{workflow.name} - Step 1",
            input_json=merged_inputs,
            status="pending",
            priority=priority,
            timeout_seconds=int(task_config.get("timeout_seconds", 300) or 300),
        )
        db.session.add(task)
        db.session.flush()

        from app.tasks.agent_tasks import run_agent_task

        celery_result = run_agent_task.apply_async(
            args=[str(task.id)],
            queue=queue_name,
            countdown=0,
        )
        task.celery_task_id = celery_result.id

        workflow.last_run_at = datetime.utcnow()
        workflow.last_run_status = "queued"
        workflow.run_count = int(workflow.run_count or 0) + 1

        db.session.add(
            UsageRecord(
                org_id=g.org.id,
                user_id=current_user.id,
                task_id=task.id,
                usage_type="task_run",
                units_consumed=1,
            )
        )

        _create_audit_log(
            action="workflow.run",
            resource_type="workflow",
            resource_id=str(workflow.id),
            extra_json={
                "task_id": str(task.id),
                "priority": priority,
            },
        )

        db.session.commit()
    except Exception:
        db.session.rollback()
        return error_response("Could not run workflow.", status=500)

    monitor_url = f"/tasks/{task.id}/monitor"
    if _is_json_request():
        return success_response({"task_id": str(task.id), "monitor_url": monitor_url})

    flash("Workflow run queued successfully.", "success")
    return redirect(monitor_url)


@workflows_bp.post("/workflows/<wf_id>/duplicate")
@login_required
@org_required
def duplicate_workflow(wf_id: str):
    """Duplicate an existing workflow into current organization workspace."""

    workflow = _get_workflow_or_404(wf_id)

    duplicated_steps = json.loads(json.dumps(workflow.steps_json if isinstance(workflow.steps_json, list) else []))
    duplicated_tags = json.loads(json.dumps(workflow.tags_json if isinstance(workflow.tags_json, list) else []))

    new_workflow = Workflow(
        org_id=g.org.id,
        project_id=workflow.project_id,
        created_by=current_user.id,
        name=f"Copy of {workflow.name}"[:255],
        description=workflow.description,
        steps_json=duplicated_steps,
        trigger_type=workflow.trigger_type,
        is_public=workflow.is_public,
        version=1,
        run_count=0,
        last_run_at=None,
        last_run_status=None,
        tags_json=duplicated_tags,
        created_at=datetime.utcnow(),
    )

    try:
        db.session.add(new_workflow)
        db.session.flush()
        _create_audit_log(
            action="workflow.duplicated",
            resource_type="workflow",
            resource_id=str(new_workflow.id),
            extra_json={"source_workflow_id": str(workflow.id)},
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        return error_response("Could not duplicate workflow.", status=500)

    return success_response(
        {
            "workflow_id": str(new_workflow.id),
            "redirect": f"/workflows/{new_workflow.id}?mode=edit",
        }
    )
