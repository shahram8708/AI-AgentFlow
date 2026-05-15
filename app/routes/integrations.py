"""Integrations blueprint routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user
from sqlalchemy import or_

from app.extensions import db
from app.models import AuditLog, CredentialVault, DataSource, Integration
from app.routes.auth import oauth
from app.services.encryption import EncryptionError, encryption_service
from app.utils.decorators import login_required, org_required
from app.utils.response_helpers import error_response, success_response
from app.utils.validators import validate_uuid

integrations_bp = Blueprint("integrations", __name__)


def _is_json_request() -> bool:
    requested_with = (request.headers.get("X-Requested-With") or "").strip().lower()
    return (
        request.is_json
        or request.accept_mimetypes.best == "application/json"
        or requested_with == "xmlhttprequest"
    )


def _client_metadata() -> tuple[str | None, str | None]:
    user_agent = request.user_agent.string if request.user_agent else None
    return request.remote_addr, user_agent[:500] if user_agent else None


def _write_audit_log(
    action: str,
    resource_id: str | None = None,
    extra_json: dict[str, Any] | None = None,
) -> None:
    ip_address, user_agent = _client_metadata()
    db.session.add(
        AuditLog(
            org_id=g.org.id,
            user_id=current_user.id,
            action=action,
            resource_type="integration",
            resource_id=resource_id,
            ip_address=ip_address,
            user_agent=user_agent,
            extra_json=extra_json,
        )
    )


def _load_integration_or_404(int_id: str) -> Integration:
    if not validate_uuid(int_id):
        abort(404)

    integration = Integration.query.filter_by(id=UUID(int_id), is_active=True).first()
    if integration is None:
        abort(404)

    return integration


def _integration_connection_map(org_id: UUID) -> dict[str, DataSource]:
    connections = DataSource.query.filter_by(
        org_id=org_id,
        is_active=True,
        is_deleted=False,
    ).all()
    return {row.source_type: row for row in connections}


def _connection_name(integration: Integration, submitted_name: str | None) -> str:
    if submitted_name:
        trimmed = submitted_name.strip()
        if trimmed:
            return trimmed[:255]
    return f"{integration.display_name} Connection"[:255]


def _simulate_oauth_connection(integration: Integration) -> DataSource:
    existing = DataSource.query.filter_by(
        org_id=g.org.id,
        integration_id=integration.id,
        is_deleted=False,
    ).first()

    config = {
        "auth_type": "oauth",
        "status": "simulated",
        "note": "OAuth tokens would be stored here in production",
    }

    if existing:
        existing.config_json = config
        existing.is_active = True
        existing.name = _connection_name(integration, existing.name)
        return existing

    datasource = DataSource(
        org_id=g.org.id,
        integration_id=integration.id,
        source_type=integration.service_name,
        name=_connection_name(integration, None),
        config_json=config,
        created_by=current_user.id,
        is_active=True,
    )
    db.session.add(datasource)
    return datasource


@integrations_bp.get("/integrations")
@login_required
@org_required
def integrations_home():
    """Render integration hub with filters and connection state."""

    category_filter = str(request.args.get("category") or "all").strip()
    search_filter = str(request.args.get("search") or "").strip()
    status_filter = str(request.args.get("status") or "all").strip().lower()

    query = Integration.query.filter_by(is_active=True)

    if category_filter and category_filter.lower() != "all":
        query = query.filter(Integration.category == category_filter)

    if search_filter:
        pattern = f"%{search_filter}%"
        query = query.filter(
            or_(
                Integration.display_name.ilike(pattern),
                Integration.description.ilike(pattern),
            )
        )

    integrations = query.order_by(Integration.display_name.asc()).all()
    connected_integrations = _integration_connection_map(g.org.id)

    if status_filter == "connected":
        integrations = [
            integration
            for integration in integrations
            if integration.service_name in connected_integrations
        ]
    elif status_filter == "not_connected":
        integrations = [
            integration
            for integration in integrations
            if integration.service_name not in connected_integrations
        ]

    categories = [
        row[0]
        for row in db.session.query(Integration.category)
        .filter(Integration.is_active.is_(True))
        .distinct()
        .order_by(Integration.category.asc())
        .all()
    ]

    category_counts: dict[str, int] = {}
    for integration in Integration.query.filter_by(is_active=True).all():
        category_counts[integration.category] = category_counts.get(integration.category, 0) + 1

    return render_template(
        "app/integrations.html",
        integrations=integrations,
        connected_integrations=connected_integrations,
        categories=categories,
        category_counts=category_counts,
        selected_category=category_filter,
        selected_status=status_filter,
        search_term=search_filter,
        total_count=len(integrations),
        connected_count=len(connected_integrations),
    )


@integrations_bp.get("/integrations/<int_id>/setup")
@login_required
@org_required
def integration_setup(int_id: str):
    """Render setup page for a specific integration connector."""

    integration = _load_integration_or_404(int_id)

    existing_connection = DataSource.query.filter_by(
        org_id=g.org.id,
        integration_id=integration.id,
        is_deleted=False,
    ).first()

    return render_template(
        "app/integration_setup.html",
        integration=integration,
        existing_connection=existing_connection,
        auth_type=integration.auth_type,
        current_year=datetime.utcnow().year,
    )


@integrations_bp.post("/integrations/<int_id>/connect")
@login_required
@org_required
def connect_integration(int_id: str):
    """Connect integration using API key or OAuth flow initiation."""

    integration = _load_integration_or_404(int_id)
    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict(flat=True)
    payload = payload if isinstance(payload, dict) else {}

    if integration.auth_type == "api_key":
        api_key = str(payload.get("api_key") or "").strip()
        connection_name = _connection_name(integration, payload.get("name"))

        if not api_key:
            return error_response("API key is required", 400)

        try:
            encrypted_value, key_ref = encryption_service.encrypt(api_key)
        except EncryptionError as exc:
            return error_response(str(exc), 400)

        vault_entry = CredentialVault(
            org_id=g.org.id,
            user_id=current_user.id,
            service_name=integration.service_name,
            label=f"{integration.display_name} API Key",
            encrypted_value=encrypted_value,
            encryption_key_ref=key_ref,
        )
        db.session.add(vault_entry)
        db.session.flush()

        existing = DataSource.query.filter_by(
            org_id=g.org.id,
            integration_id=integration.id,
            is_deleted=False,
        ).first()

        old_vault_entry = None
        if existing and isinstance(existing.config_json, dict):
            old_vault_id = existing.config_json.get("credential_vault_id")
            if isinstance(old_vault_id, str) and validate_uuid(old_vault_id):
                old_vault_entry = CredentialVault.query.filter_by(
                    id=UUID(old_vault_id),
                    org_id=g.org.id,
                ).first()

        config_json = {
            "credential_vault_id": str(vault_entry.id),
            "auth_type": "api_key",
        }

        if existing:
            existing.name = connection_name
            existing.source_type = integration.service_name
            existing.config_json = config_json
            existing.is_active = True
            existing.deleted_at = None
        else:
            db.session.add(
                DataSource(
                    org_id=g.org.id,
                    integration_id=integration.id,
                    source_type=integration.service_name,
                    name=connection_name,
                    config_json=config_json,
                    created_by=current_user.id,
                    is_active=True,
                )
            )

        if old_vault_entry is not None:
            db.session.delete(old_vault_entry)

        _write_audit_log(
            action="integration.connected",
            resource_id=str(integration.id),
            extra_json={"integration": integration.service_name},
        )
        db.session.commit()

        return success_response(
            {"message": f"{integration.display_name} connected successfully."}
        )

    if integration.auth_type == "oauth":
        session["oauth_integration_id"] = str(integration.id)

        client = oauth.create_client(integration.service_name)
        if client is not None:
            redirect_uri = url_for("auth.oauth_callback_google", _external=True)
            response = client.authorize_redirect(redirect_uri)
            if _is_json_request():
                return success_response({"redirect_url": response.location})
            return response

        datasource = _simulate_oauth_connection(integration)
        _write_audit_log(
            action="integration.connected",
            resource_id=str(integration.id),
            extra_json={"integration": integration.service_name, "mode": "oauth_simulated"},
        )
        db.session.commit()

        message = f"{integration.display_name} connected successfully."
        if _is_json_request():
            return success_response({"message": message, "data_source_id": str(datasource.id)})

        flash(message, "success")
        return redirect(url_for("integrations.integration_setup", int_id=str(integration.id)))

    return error_response("Unsupported authentication type", 400)


@integrations_bp.delete("/integrations/<int_id>")
@login_required
@org_required
def disconnect_integration(int_id: str):
    """Disconnect integration and remove vault credential references."""

    integration = _load_integration_or_404(int_id)

    data_source = DataSource.query.filter_by(
        org_id=g.org.id,
        integration_id=integration.id,
        is_deleted=False,
    ).first()

    if data_source is None:
        return error_response("Integration not connected", 404)

    config_json = data_source.config_json or {}
    vault_id = config_json.get("credential_vault_id") if isinstance(config_json, dict) else None
    if isinstance(vault_id, str) and validate_uuid(vault_id):
        vault_entry = CredentialVault.query.filter_by(
            id=UUID(vault_id),
            org_id=g.org.id,
        ).first()
        if vault_entry is not None:
            db.session.delete(vault_entry)

    _write_audit_log(
        action="integration.disconnected",
        resource_id=str(integration.id),
        extra_json={"integration": integration.service_name},
    )
    db.session.delete(data_source)
    db.session.commit()

    return success_response({"disconnected": True})


@integrations_bp.post("/integrations/<int_id>/test")
@login_required
@org_required
def test_integration_connection(int_id: str):
    """Run a lightweight integration connectivity test."""

    integration = _load_integration_or_404(int_id)

    data_source = DataSource.query.filter_by(
        org_id=g.org.id,
        integration_id=integration.id,
        is_deleted=False,
        is_active=True,
    ).first()
    if data_source is None:
        return error_response("Integration not connected", 404)

    config_json = data_source.config_json or {}
    reason = ""

    if integration.auth_type == "api_key":
        vault_id = config_json.get("credential_vault_id") if isinstance(config_json, dict) else None
        if not isinstance(vault_id, str) or not validate_uuid(vault_id):
            return error_response("Connection test failed: Missing credential reference", 400)

        vault_entry = CredentialVault.query.filter_by(
            id=UUID(vault_id),
            org_id=g.org.id,
        ).first()
        if vault_entry is None:
            return error_response("Connection test failed: Credential not found", 400)

        try:
            api_key = encryption_service.decrypt(
                vault_entry.encrypted_value,
                vault_entry.encryption_key_ref,
            )
        except Exception:  # pylint: disable=broad-except
            return error_response("Connection test failed: Could not decrypt credential", 400)

        if len(api_key.strip()) < 12:
            reason = "API key appears too short"
        elif any(char.isspace() for char in api_key):
            reason = "API key contains invalid whitespace"

    elif integration.auth_type == "oauth":
        if not isinstance(config_json, dict) or config_json.get("auth_type") != "oauth":
            reason = "OAuth metadata missing"

    if reason:
        return error_response(f"Connection test failed: {reason}", 400)

    data_source.last_synced = datetime.utcnow()
    db.session.commit()

    return success_response({"status": "ok", "message": "Connection test passed"})
