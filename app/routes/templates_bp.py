"""Template marketplace blueprint routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from flask import Blueprint, abort, g, render_template, request
from flask_login import current_user
from sqlalchemy import Text, cast, func, or_

from app.extensions import db
from app.models import AuditLog, DataSource, Integration, Workflow, WorkflowTemplate
from app.services.agent_runner import TASK_REGISTRY
from app.utils.decorators import login_required, org_required
from app.utils.response_helpers import error_response, success_response
from app.utils.validators import validate_uuid

templates_bp = Blueprint("templates_bp", __name__)

ALLOWED_DIFFICULTIES = {"beginner", "intermediate", "advanced"}


def _template_by_id_or_404(template_id: str) -> WorkflowTemplate:
    """Load active workflow template by UUID."""

    if not validate_uuid(template_id):
        abort(404)

    template = WorkflowTemplate.query.filter_by(id=UUID(template_id), is_active=True).first()
    if template is None:
        abort(404)
    return template


def _normalize_template_steps(raw_steps: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalize template steps into workflow compatible step objects."""

    steps = raw_steps if isinstance(raw_steps, list) else []
    normalized: list[dict[str, Any]] = []
    invalid_task_types: list[str] = []

    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue

        task_type = str(step.get("task_type") or "").strip()
        if not task_type:
            continue

        task_config = TASK_REGISTRY.get(task_type)
        if not task_config:
            invalid_task_types.append(task_type)
            continue

        input_json = step.get("input_json")
        if input_json is None and isinstance(step.get("input"), dict):
            input_json = step.get("input")
        if not isinstance(input_json, dict):
            input_json = {}

        normalized.append(
            {
                "task_type": task_type,
                "task_name": str(
                    step.get("task_name")
                    or step.get("step_name")
                    or task_config.get("name")
                    or f"Step {index + 1}"
                ),
                "input_json": input_json,
                "description": str(
                    step.get("description") or task_config.get("description") or ""
                ),
                "category": str(step.get("category") or task_config.get("category") or ""),
            }
        )

    return normalized, sorted(set(invalid_task_types))


def _enrich_template_steps(raw_steps: Any) -> list[dict[str, Any]]:
    """Attach task metadata to template steps for detailed preview."""

    normalized_steps, _invalid = _normalize_template_steps(raw_steps)
    enriched: list[dict[str, Any]] = []

    for index, step in enumerate(normalized_steps, start=1):
        task_type = str(step.get("task_type") or "")
        task_config = TASK_REGISTRY.get(task_type, {})
        input_fields = task_config.get("input_fields", [])

        enriched.append(
            {
                "index": index,
                "task_type": task_type,
                "task_name": step.get("task_name") or task_config.get("name") or task_type,
                "description": step.get("description") or task_config.get("description") or "",
                "category": task_config.get("category_display") or task_config.get("category") or "General",
                "estimated_seconds": int(task_config.get("estimated_seconds", 60) or 60),
                "input_fields": input_fields if isinstance(input_fields, list) else [],
                "input_json": step.get("input_json") if isinstance(step.get("input_json"), dict) else {},
            }
        )

    return enriched


@templates_bp.get("/templates")
@login_required
@org_required
def templates_home():
    """Render template marketplace with filter and sorting controls."""

    category_filter = str(request.args.get("category") or "").strip()
    difficulty_filter = str(request.args.get("difficulty") or "").strip().lower()
    search_query = str(request.args.get("search") or "").strip()
    sort_key = str(request.args.get("sort") or "featured").strip().lower()
    page = max(request.args.get("page", default=1, type=int), 1)

    query = WorkflowTemplate.query.filter_by(is_active=True)

    if category_filter and category_filter.lower() != "all":
        query = query.filter(WorkflowTemplate.category == category_filter)

    if difficulty_filter and difficulty_filter in ALLOWED_DIFFICULTIES:
        query = query.filter(WorkflowTemplate.difficulty == difficulty_filter)

    if search_query:
        pattern = f"%{search_query}%"
        query = query.filter(
            or_(
                WorkflowTemplate.name.ilike(pattern),
                WorkflowTemplate.description.ilike(pattern),
                cast(WorkflowTemplate.tags_json, Text).ilike(pattern),
            )
        )

    if sort_key == "newest":
        query = query.order_by(WorkflowTemplate.created_at.desc())
    elif sort_key == "alphabetical":
        query = query.order_by(WorkflowTemplate.name.asc())
    elif sort_key == "most_used":
        query = query.order_by(
            WorkflowTemplate.usage_count.desc(),
            WorkflowTemplate.created_at.desc(),
        )
    else:
        query = query.order_by(
            WorkflowTemplate.is_featured.desc(),
            WorkflowTemplate.usage_count.desc(),
            WorkflowTemplate.created_at.desc(),
        )

    pagination = query.paginate(page=page, per_page=12, error_out=False)

    featured_templates = (
        WorkflowTemplate.query.filter_by(is_featured=True, is_active=True)
        .order_by(WorkflowTemplate.usage_count.desc(), WorkflowTemplate.created_at.desc())
        .limit(6)
        .all()
    )

    category_rows = (
        db.session.query(
            WorkflowTemplate.category,
            func.count(WorkflowTemplate.id).label("total"),
        )
        .filter(WorkflowTemplate.is_active.is_(True))
        .group_by(WorkflowTemplate.category)
        .order_by(WorkflowTemplate.category.asc())
        .all()
    )
    category_counts = {str(row.category): int(row.total or 0) for row in category_rows}

    return render_template(
        "app/templates.html",
        template_pagination=pagination,
        templates=pagination.items,
        featured_templates=featured_templates,
        category_counts=category_counts,
        all_categories=list(category_counts.keys()),
        filter_category=category_filter,
        filter_difficulty=difficulty_filter,
        filter_search=search_query,
        filter_sort=sort_key,
    )


@templates_bp.get("/templates/<tpl_id>")
@login_required
@org_required
def template_detail(tpl_id: str):
    """Render individual template preview and integration requirements."""

    template = _template_by_id_or_404(tpl_id)

    steps_enriched = _enrich_template_steps(template.steps_json)

    required_integrations_raw = (
        template.required_integrations
        if isinstance(template.required_integrations, list)
        else []
    )
    required_values = [str(item).strip() for item in required_integrations_raw if str(item).strip()]

    integration_uuid_values = [UUID(value) for value in required_values if validate_uuid(value)]
    integration_service_values = [value for value in required_values if not validate_uuid(value)]

    integration_query = Integration.query.filter(Integration.is_active.is_(True))
    if integration_uuid_values and integration_service_values:
        integrations = integration_query.filter(
            or_(
                Integration.id.in_(integration_uuid_values),
                Integration.service_name.in_(integration_service_values),
            )
        ).all()
    elif integration_uuid_values:
        integrations = integration_query.filter(Integration.id.in_(integration_uuid_values)).all()
    elif integration_service_values:
        integrations = integration_query.filter(Integration.service_name.in_(integration_service_values)).all()
    else:
        integrations = []

    org_data_sources = (
        DataSource.query.filter_by(org_id=g.org.id, is_active=True, is_deleted=False)
        .all()
    )
    connected_integration_ids = {
        source.integration_id for source in org_data_sources if source.integration_id is not None
    }
    connected_source_types = {
        str(source.source_type).strip().lower()
        for source in org_data_sources
        if source.source_type
    }

    integration_cards: list[dict[str, Any]] = []
    covered_required_keys: set[str] = set()
    for integration in integrations:
        service_key = integration.service_name.strip().lower()
        covered_required_keys.add(service_key)
        covered_required_keys.add(str(integration.id))

        is_connected = (
            integration.id in connected_integration_ids
            or service_key in connected_source_types
        )

        integration_cards.append(
            {
                "id": str(integration.id),
                "service_name": integration.service_name,
                "display_name": integration.display_name,
                "auth_type": integration.auth_type,
                "logo_url": integration.logo_url,
                "is_connected": is_connected,
            }
        )

    missing_required_keys = [
        value for value in required_values if value.strip().lower() not in covered_required_keys
    ]
    for value in missing_required_keys:
        integration_cards.append(
            {
                "id": value,
                "service_name": value,
                "display_name": value.replace("_", " ").title(),
                "auth_type": "api_key",
                "logo_url": "",
                "is_connected": False,
            }
        )

    related_templates = (
        WorkflowTemplate.query.filter(
            WorkflowTemplate.is_active.is_(True),
            WorkflowTemplate.category == template.category,
            WorkflowTemplate.id != template.id,
        )
        .order_by(WorkflowTemplate.is_featured.desc(), WorkflowTemplate.usage_count.desc())
        .limit(3)
        .all()
    )

    return render_template(
        "app/template_detail.html",
        template=template,
        steps_enriched=steps_enriched,
        integration_cards=integration_cards,
        related_templates=related_templates,
        missing_integrations_count=sum(1 for card in integration_cards if not card["is_connected"]),
    )


@templates_bp.post("/templates/<tpl_id>/use")
@login_required
@org_required
def use_template(tpl_id: str):
    """Create workflow from template for current organization."""

    template = _template_by_id_or_404(tpl_id)

    normalized_steps, invalid_task_types = _normalize_template_steps(template.steps_json)
    if invalid_task_types:
        invalid_list = ", ".join(invalid_task_types)
        return error_response(f"Template contains invalid task types: {invalid_list}", status=400)

    workflow = Workflow(
        name=template.name[:255],
        description=(template.description or "")[:1000] or None,
        steps_json=normalized_steps,
        org_id=g.org.id,
        created_by=current_user.id,
        tags_json=template.tags_json if isinstance(template.tags_json, list) else [],
        trigger_type="manual",
        version=1,
        run_count=0,
        is_public=False,
        created_at=datetime.utcnow(),
    )

    try:
        db.session.add(workflow)
        db.session.flush()

        template.usage_count = int(template.usage_count or 0) + 1

        db.session.add(
            AuditLog(
                org_id=g.org.id,
                user_id=current_user.id,
                action="template.used",
                resource_type="workflow_template",
                resource_id=str(template.id),
                ip_address=request.remote_addr,
                user_agent=(request.user_agent.string[:500] if request.user_agent else None),
                extra_json={
                    "template_id": str(template.id),
                    "template_name": template.name,
                    "workflow_id": str(workflow.id),
                },
            )
        )

        db.session.commit()
    except Exception:
        db.session.rollback()
        return error_response("Could not create workflow from template.", status=500)

    return success_response(
        {
            "workflow_id": str(workflow.id),
            "redirect": f"/workflows/{workflow.id}?mode=edit",
        }
    )
