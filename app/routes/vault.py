"""Credentials vault blueprint routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from flask import Blueprint, abort, g, render_template, request
from flask_login import current_user

from app.extensions import db, limiter
from app.models import AuditLog, CredentialVault, User
from app.services.encryption import DecryptionError, EncryptionError, encryption_service
from app.utils.decorators import admin_required, login_required, org_required
from app.utils.response_helpers import error_response, success_response
from app.utils.validators import validate_uuid

vault_bp = Blueprint("vault", __name__)


def _client_metadata() -> tuple[str | None, str | None]:
    user_agent = request.user_agent.string if request.user_agent else None
    return request.remote_addr, user_agent[:500] if user_agent else None


def _write_audit_log(action: str, resource_id: str | None = None, extra_json: dict[str, Any] | None = None) -> None:
    ip_address, user_agent = _client_metadata()
    db.session.add(
        AuditLog(
            org_id=g.org.id,
            user_id=current_user.id,
            action=action,
            resource_type="vault",
            resource_id=resource_id,
            ip_address=ip_address,
            user_agent=user_agent,
            extra_json=extra_json,
        )
    )


def _get_entry_or_404(entry_id: str) -> CredentialVault:
    if not validate_uuid(entry_id):
        abort(404)

    entry = CredentialVault.query.filter_by(id=UUID(entry_id), org_id=g.org.id).first()
    if entry is None:
        abort(404)
    return entry


@vault_bp.get("/vault")
@login_required
@org_required
def vault_home():
    """Render credentials vault list without encrypted values."""

    entries = (
        CredentialVault.query.filter_by(org_id=g.org.id)
        .order_by(CredentialVault.service_name.asc(), CredentialVault.label.asc())
        .all()
    )

    user_ids = {entry.user_id for entry in entries}
    users = {user.id: user for user in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {}

    metadata_entries: list[dict[str, Any]] = []
    for entry in entries:
        created_by = users.get(entry.user_id)
        metadata_entries.append(
            {
                "id": str(entry.id),
                "service_name": entry.service_name,
                "label": entry.label,
                "encryption_key_ref": entry.encryption_key_ref,
                "created_at": entry.created_at,
                "last_used": entry.last_used,
                "user_id": str(entry.user_id),
                "created_by_name": created_by.get_full_name() if created_by else "Unknown",
            }
        )

    return render_template(
        "app/vault.html",
        entries=metadata_entries,
        total_entries=len(metadata_entries),
    )


@vault_bp.post("/vault")
@login_required
@org_required
def create_vault_credential():
    """Create an encrypted vault credential entry."""

    payload = request.get_json(silent=True) or {}
    service_name = str(payload.get("service_name") or "").strip()
    label = str(payload.get("label") or "").strip()
    credential_value = str(payload.get("credential_value") or "").strip()

    if not service_name or not label or not credential_value:
        return error_response("service_name, label, and credential_value are required", 400)

    if len(service_name) > 100:
        return error_response("service_name must be at most 100 characters", 400)

    if len(label) > 255:
        return error_response("label must be at most 255 characters", 400)

    try:
        encrypted_value, key_ref = encryption_service.encrypt(credential_value)
    except EncryptionError as exc:
        return error_response(str(exc), 400)

    entry = CredentialVault(
        org_id=g.org.id,
        user_id=current_user.id,
        service_name=service_name,
        label=label,
        encrypted_value=encrypted_value,
        encryption_key_ref=key_ref,
    )

    db.session.add(entry)
    db.session.flush()
    _write_audit_log(
        action="vault.credential_added",
        resource_id=str(entry.id),
        extra_json={"service_name": service_name, "label": label},
    )
    db.session.commit()

    return success_response(
        {
            "id": str(entry.id),
            "service_name": service_name,
            "label": label,
        }
    )


@vault_bp.post("/vault/<entry_id>/reveal")
@login_required
@org_required
@limiter.limit("10 per hour")
def reveal_vault_credential(entry_id: str):
    """Reveal a credential value after password reauthentication."""

    payload = request.get_json(silent=True) or {}
    password = str(payload.get("password") or "")

    entry = _get_entry_or_404(entry_id)

    if not current_user.check_password(password):
        _write_audit_log(
            action="vault.reveal_failed",
            resource_id=str(entry.id),
            extra_json={"service_name": entry.service_name, "label": entry.label},
        )
        db.session.commit()
        return error_response("Invalid password", 403)

    try:
        plaintext = encryption_service.decrypt(entry.encrypted_value, entry.encryption_key_ref)
    except (EncryptionError, DecryptionError):
        _write_audit_log(
            action="vault.reveal_failed",
            resource_id=str(entry.id),
            extra_json={"service_name": entry.service_name, "label": entry.label},
        )
        db.session.commit()
        return error_response("Unable to reveal credential", 400)

    entry.last_used = datetime.utcnow()
    _write_audit_log(
        action="vault.credential_revealed",
        resource_id=str(entry.id),
        extra_json={"service_name": entry.service_name, "label": entry.label},
    )
    db.session.commit()

    return success_response({"value": plaintext})


@vault_bp.delete("/vault/<entry_id>")
@login_required
@org_required
def delete_vault_credential(entry_id: str):
    """Delete a vault credential entry."""

    entry = _get_entry_or_404(entry_id)

    _write_audit_log(
        action="vault.credential_deleted",
        resource_id=str(entry.id),
        extra_json={"service_name": entry.service_name, "label": entry.label},
    )
    db.session.delete(entry)
    db.session.commit()

    return success_response({"deleted": True})


@vault_bp.put("/vault/<entry_id>")
@login_required
@org_required
def update_vault_credential(entry_id: str):
    """Update vault credential metadata without updating secret value."""

    entry = _get_entry_or_404(entry_id)
    payload = request.get_json(silent=True) or {}

    if "credential_value" in payload:
        return error_response("Credential value cannot be updated from this endpoint", 400)

    label = payload.get("label")
    service_name = payload.get("service_name")

    if label is not None:
        label = str(label).strip()
        if not label:
            return error_response("label cannot be empty", 400)
        if len(label) > 255:
            return error_response("label must be at most 255 characters", 400)
        entry.label = label

    if service_name is not None:
        service_name = str(service_name).strip()
        if not service_name:
            return error_response("service_name cannot be empty", 400)
        if len(service_name) > 100:
            return error_response("service_name must be at most 100 characters", 400)
        entry.service_name = service_name

    _write_audit_log(
        action="vault.credential_updated",
        resource_id=str(entry.id),
        extra_json={"service_name": entry.service_name, "label": entry.label},
    )
    db.session.commit()

    return success_response({"updated": True})


@vault_bp.get("/vault/verify-integrity")
@login_required
@org_required
@admin_required
def verify_vault_integrity():
    """Run org scoped vault integrity check."""

    entries = CredentialVault.query.filter_by(org_id=g.org.id).all()

    passed = 0
    failed_ids: list[str] = []
    for entry in entries:
        if encryption_service.verify_integrity(entry.encrypted_value, entry.encryption_key_ref):
            passed += 1
        else:
            failed_ids.append(str(entry.id))

    payload = {
        "entries": len(entries),
        "passing": passed,
        "failing": len(failed_ids),
        "failing_entry_ids": failed_ids,
    }
    return success_response(payload)
