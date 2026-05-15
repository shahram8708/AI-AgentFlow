"""Settings blueprint routes."""

from __future__ import annotations

import os
import re
import secrets
from datetime import datetime, timezone
from uuid import UUID

import bcrypt
from PIL import Image, UnidentifiedImageError
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from flask_login import current_user, logout_user
from sqlalchemy import case

from app.extensions import cache, db
from app.forms.settings import AccountSettingsForm, ChangePasswordForm, DeleteAccountForm, OrgSettingsForm
from app.models import ApiKey, AuditLog, AutomationTask, OrganizationMember, Plan, UsageRecord
from app.services.billing_service import BillingServiceError, billing_service
from app.services.file_service import FileService
from app.utils.decorators import login_required, org_required
from app.utils.response_helpers import error_response, success_response
from app.utils.validators import validate_uuid

settings_bp = Blueprint("settings", __name__)
file_service = FileService()

ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}
MAX_AVATAR_SIZE_BYTES = 5 * 1024 * 1024


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_json_request() -> bool:
    requested_with = (request.headers.get("X-Requested-With") or "").strip().lower()
    return (
        request.is_json
        or request.accept_mimetypes.best == "application/json"
        or requested_with == "xmlhttprequest"
    )


def _current_org_membership() -> OrganizationMember | None:
    return OrganizationMember.query.filter_by(org_id=g.org.id, user_id=current_user.id).first()


def _plan_has_api_access(plan: Plan | None) -> bool:
    if plan is None:
        return False
    if plan.slug in {"pro", "team", "enterprise"}:
        return True

    features = plan.features_json or []
    if isinstance(features, dict):
        return bool(features.get("api_access") or features.get("all_features"))
    if isinstance(features, list):
        return "api_access" in features or "all_features" in features
    return False


def _save_image_thumbnail(file_storage, output_path: str, extension: str) -> None:
    image = Image.open(file_storage)
    image.thumbnail((200, 200))

    ext = extension.lower()
    if ext in {"jpg", "jpeg"}:
        if image.mode in {"RGBA", "LA", "P"}:
            image = image.convert("RGB")
        image.save(output_path, format="JPEG", quality=90, optimize=True)
    elif ext == "png":
        image.save(output_path, format="PNG", optimize=True)
    elif ext == "webp":
        image.save(output_path, format="WEBP", quality=90, method=6)
    elif ext == "gif":
        image.save(output_path, format="GIF")
    else:
        raise ValueError("Unsupported image extension")


def _validate_image_upload(file_storage) -> tuple[bool, str | None, str | None]:
    if file_storage is None or not file_storage.filename:
        return False, "Please choose an image to upload.", None

    if "." not in file_storage.filename:
        return False, "Invalid file format.", None

    extension = file_storage.filename.rsplit(".", 1)[1].lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        return False, "Please upload JPG, JPEG, PNG, WEBP, or GIF image files only.", None

    file_storage.seek(0, os.SEEK_END)
    size = file_storage.tell()
    file_storage.seek(0)

    if size > MAX_AVATAR_SIZE_BYTES:
        return False, "Image must be under 5MB.", None

    return True, None, extension


def _serialize_scopes(raw_scopes) -> list[str]:
    if isinstance(raw_scopes, list):
        scopes = [str(scope).strip().lower() for scope in raw_scopes if str(scope).strip()]
        return scopes
    if isinstance(raw_scopes, str):
        scopes = [scope.strip().lower() for scope in raw_scopes.split(",") if scope.strip()]
        return scopes
    return []


@settings_bp.route("/settings/account", methods=["GET", "POST"])
@login_required
def account_settings():
    """Render and process account level settings forms."""

    preferences = dict(current_user.preferences_json or {})
    notification_prefs = dict(preferences.get("email_notifications") or {})

    account_form = AccountSettingsForm(
        data={
            "first_name": current_user.first_name,
            "last_name": current_user.last_name,
            "timezone": preferences.get("timezone", "Asia/Kolkata"),
            "email_notifications_tasks": notification_prefs.get("tasks", True),
            "email_notifications_billing": notification_prefs.get("billing", True),
            "email_notifications_team": notification_prefs.get("team", True),
            "email_notifications_weekly_digest": notification_prefs.get("weekly_digest", True),
        }
    )
    password_form = ChangePasswordForm()
    delete_form = DeleteAccountForm()

    if request.method == "POST":
        form_type = str(request.form.get("form_type") or "").strip().lower()

        if form_type == "profile":
            account_form = AccountSettingsForm(formdata=request.form)
            if not account_form.validate():
                flash("Please correct the profile form errors and try again.", "danger")
                return render_template(
                    "app/settings_account.html",
                    account_form=account_form,
                    password_form=password_form,
                    delete_form=delete_form,
                    mfa_enabled=bool(current_user.mfa_secret),
                    avatar_url=current_user.avatar_url,
                    active_session_device=f"{request.user_agent.platform or 'Web'} on {request.user_agent.browser or 'Browser'}",
                    last_mfa_event=None,
                ), 400

            current_user.first_name = (account_form.first_name.data or "").strip()
            current_user.last_name = (account_form.last_name.data or "").strip()

            updated_preferences = dict(current_user.preferences_json or {})
            existing_notification_prefs = dict(updated_preferences.get("email_notifications") or {})

            def _checkbox_value(field_name: str, fallback: bool) -> bool:
                if field_name not in request.form:
                    return bool(fallback)
                return bool(getattr(account_form, field_name).data)

            updated_preferences["timezone"] = account_form.timezone.data
            updated_preferences["email_notifications"] = {
                "tasks": _checkbox_value("email_notifications_tasks", existing_notification_prefs.get("tasks", True)),
                "billing": _checkbox_value("email_notifications_billing", existing_notification_prefs.get("billing", True)),
                "team": _checkbox_value("email_notifications_team", existing_notification_prefs.get("team", True)),
                "weekly_digest": _checkbox_value(
                    "email_notifications_weekly_digest",
                    existing_notification_prefs.get("weekly_digest", True),
                ),
            }
            current_user.preferences_json = updated_preferences

            db.session.add(
                AuditLog(
                    org_id=None,
                    user_id=current_user.id,
                    action="settings.profile_updated",
                    resource_type="user",
                    resource_id=str(current_user.id),
                    ip_address=request.remote_addr,
                    user_agent=(request.user_agent.string or "")[:500],
                )
            )
            db.session.commit()

            cache.delete(f"dashboard_stats_{current_user.id}")
            cache.delete(f"user_org:{current_user.id}")

            flash("Account settings saved successfully.", "success")
            return redirect(url_for("settings.account_settings"))

        if form_type == "change_password":
            password_form = ChangePasswordForm(formdata=request.form)
            if not password_form.validate():
                flash("Please correct the password form errors and try again.", "danger")
                return redirect(url_for("settings.account_settings", anchor="security-section"))

            if not current_user.check_password(password_form.current_password.data or ""):
                flash("Your current password is incorrect.", "danger")
                return redirect(url_for("settings.account_settings", anchor="security-section"))

            current_user.set_password(password_form.new_password.data or "")
            db.session.add(
                AuditLog(
                    user_id=current_user.id,
                    action="settings.password_changed",
                    resource_type="user",
                    resource_id=str(current_user.id),
                    ip_address=request.remote_addr,
                    user_agent=(request.user_agent.string or "")[:500],
                )
            )
            db.session.commit()

            logout_user()
            session.clear()
            flash("Password changed successfully. Please sign in again for security.", "success")
            return redirect("/auth/login")

        if form_type == "delete_account":
            delete_form = DeleteAccountForm(formdata=request.form)
            if not delete_form.validate():
                flash("Please complete the account deletion confirmation correctly.", "danger")
                return redirect(url_for("settings.account_settings", anchor="danger-section"))

            if not current_user.check_password(delete_form.password.data or ""):
                flash("Incorrect password. Account deletion was cancelled.", "danger")
                return redirect(url_for("settings.account_settings", anchor="danger-section"))

            running_tasks = AutomationTask.query.filter_by(
                user_id=current_user.id,
                status="running",
            ).count()
            if running_tasks > 0:
                flash(
                    "You still have running tasks. Please wait for them to finish before deleting your account.",
                    "warning",
                )
                return redirect(url_for("settings.account_settings", anchor="danger-section"))

            try:
                organization = None
                membership = OrganizationMember.query.filter_by(user_id=current_user.id).first()
                if membership:
                    organization = membership.organization
                if organization is not None:
                    try:
                        billing_service.cancel_subscription(
                            str(organization.id),
                            reason="Account owner requested account deletion",
                        )
                    except BillingServiceError as exc:
                        if "No active subscription found" not in str(exc):
                            current_app.logger.warning(
                                "Subscription cancellation during account delete failed: %s",
                                exc,
                            )

                current_user.is_active = False
                current_user.email = f"deleted_{current_user.id}@deleted.agentflow.ai"
                current_user.password_hash = None

                db.session.add(
                    AuditLog(
                        org_id=membership.org_id if membership else None,
                        user_id=current_user.id,
                        action="user.account_deleted",
                        resource_type="user",
                        resource_id=str(current_user.id),
                        ip_address=request.remote_addr,
                        user_agent=(request.user_agent.string or "")[:500],
                    )
                )
                db.session.commit()
            except Exception as exc:  # pylint: disable=broad-except
                db.session.rollback()
                current_app.logger.error("Failed to delete account for user %s: %s", current_user.id, exc)
                flash("Unable to delete your account right now. Please contact support.", "danger")
                return redirect(url_for("settings.account_settings"))

            logout_user()
            session.clear()
            flash("Your account has been deleted.", "info")
            return redirect("/")

        flash("Unsupported account settings request.", "warning")
        return redirect(url_for("settings.account_settings"))

    last_mfa_event = (
        AuditLog.query.filter(
            AuditLog.user_id == current_user.id,
            AuditLog.action.in_(["user.mfa_enabled", "user.mfa_disabled", "user.mfa_failed"]),
        )
        .order_by(AuditLog.timestamp.desc())
        .first()
    )

    return render_template(
        "app/settings_account.html",
        account_form=account_form,
        password_form=password_form,
        delete_form=delete_form,
        mfa_enabled=bool(current_user.mfa_secret),
        avatar_url=current_user.avatar_url,
        active_session_device=f"{request.user_agent.platform or 'Web'} on {request.user_agent.browser or 'Browser'}",
        last_mfa_event=last_mfa_event,
    )


@settings_bp.post("/settings/account/avatar")
@login_required
def upload_avatar():
    """Upload and update user avatar image."""

    avatar = request.files.get("avatar")
    is_valid, error_message, extension = _validate_image_upload(avatar)
    if not is_valid or extension is None:
        return error_response(error_message or "Invalid avatar upload.", 400)

    upload_root = current_app.config.get("UPLOAD_FOLDER") or file_service.upload_folder
    avatar_dir = os.path.join(upload_root, "avatars")
    os.makedirs(avatar_dir, exist_ok=True)

    filename = f"{current_user.id}.{extension}"
    output_path = os.path.join(avatar_dir, filename)

    try:
        _save_image_thumbnail(avatar, output_path, extension)
    except (UnidentifiedImageError, ValueError):
        return error_response("Uploaded file is not a valid image.", 400)
    except Exception as exc:  # pylint: disable=broad-except
        current_app.logger.error("Failed to save avatar for user %s: %s", current_user.id, exc)
        return error_response("Unable to save avatar right now.", 500)

    current_user.avatar_url = f"/uploads/avatars/{filename}"
    db.session.add(
        AuditLog(
            user_id=current_user.id,
            action="settings.avatar_updated",
            resource_type="user",
            resource_id=str(current_user.id),
            ip_address=request.remote_addr,
            user_agent=(request.user_agent.string or "")[:500],
        )
    )
    db.session.commit()

    return success_response({"avatar_url": current_user.avatar_url})


@settings_bp.route("/settings/organization", methods=["GET", "POST"])
@login_required
@org_required
def organization_settings():
    """Render and process organization settings forms."""

    membership = _current_org_membership()
    if membership is None or membership.role not in {"owner", "admin"}:
        abort(403)

    org_settings = dict(g.org.settings_json or {})
    branding_settings = dict(org_settings.get("branding") or {})
    sso_settings = dict(org_settings.get("sso") or {})

    form = OrgSettingsForm(
        data={
            "name": g.org.name,
            "slug": g.org.slug,
            "timezone": org_settings.get("timezone", "Asia/Kolkata"),
        }
    )

    if request.method == "POST":
        form_type = str(request.form.get("form_type") or "general").strip().lower()

        if form_type == "branding":
            brand_color = (request.form.get("brand_color") or "").strip()
            if brand_color and not re.match(r"^#[0-9A-Fa-f]{6}$", brand_color):
                flash("Please provide a valid hex color for branding.", "danger")
                return redirect(url_for("settings.organization_settings", anchor="branding-section"))

            logo_file = request.files.get("org_logo")
            if logo_file and logo_file.filename:
                is_valid, error_message, extension = _validate_image_upload(logo_file)
                if not is_valid or extension is None:
                    flash(error_message or "Invalid logo upload.", "danger")
                    return redirect(url_for("settings.organization_settings", anchor="branding-section"))

                upload_root = current_app.config.get("UPLOAD_FOLDER") or file_service.upload_folder
                logo_dir = os.path.join(upload_root, "avatars")
                os.makedirs(logo_dir, exist_ok=True)
                logo_name = f"org_{g.org.id}.{extension}"
                logo_path = os.path.join(logo_dir, logo_name)

                try:
                    _save_image_thumbnail(logo_file, logo_path, extension)
                except (UnidentifiedImageError, ValueError):
                    flash("Uploaded logo file is not a valid image.", "danger")
                    return redirect(url_for("settings.organization_settings", anchor="branding-section"))

                branding_settings["logo_url"] = f"/uploads/avatars/{logo_name}"

            if brand_color:
                branding_settings["color"] = brand_color

            org_settings["branding"] = branding_settings
            g.org.settings_json = org_settings

            db.session.add(
                AuditLog(
                    org_id=g.org.id,
                    user_id=current_user.id,
                    action="settings.org_branding_updated",
                    resource_type="organization",
                    resource_id=str(g.org.id),
                    ip_address=request.remote_addr,
                    user_agent=(request.user_agent.string or "")[:500],
                )
            )
            db.session.commit()

            flash("Organization branding updated successfully.", "success")
            return redirect(url_for("settings.organization_settings", anchor="branding-section"))

        if form_type == "sso":
            plan = db.session.get(Plan, g.org.plan_id) if g.org.plan_id else None
            if plan is None or plan.slug not in {"team", "enterprise"}:
                flash("SSO is available only on Team and Enterprise plans.", "warning")
                return redirect(url_for("settings.organization_settings", anchor="sso-section"))

            metadata_url = (request.form.get("saml_metadata_url") or "").strip()
            is_enabled = bool(request.form.get("sso_enabled"))
            sso_settings.update(
                {
                    "enabled": is_enabled,
                    "saml_metadata_url": metadata_url,
                    "updated_by": str(current_user.id),
                    "updated_at": _utcnow().isoformat(),
                }
            )
            org_settings["sso"] = sso_settings
            g.org.settings_json = org_settings

            db.session.add(
                AuditLog(
                    org_id=g.org.id,
                    user_id=current_user.id,
                    action="settings.org_sso_updated",
                    resource_type="organization",
                    resource_id=str(g.org.id),
                    ip_address=request.remote_addr,
                    user_agent=(request.user_agent.string or "")[:500],
                )
            )
            db.session.commit()

            flash("SSO settings saved.", "success")
            return redirect(url_for("settings.organization_settings", anchor="sso-section"))

        form = OrgSettingsForm(formdata=request.form)
        if not form.validate_on_submit():
            flash("Please correct the organization form errors and try again.", "danger")
            return render_template(
                "app/settings_org.html",
                form=form,
                branding=branding_settings,
                sso=sso_settings,
                plan=db.session.get(Plan, g.org.plan_id) if g.org.plan_id else None,
            ), 400

        org_settings["timezone"] = form.timezone.data
        g.org.name = (form.name.data or "").strip()
        g.org.slug = (form.slug.data or "").strip().lower()
        g.org.settings_json = org_settings

        db.session.add(
            AuditLog(
                org_id=g.org.id,
                user_id=current_user.id,
                action="settings.org_updated",
                resource_type="organization",
                resource_id=str(g.org.id),
                ip_address=request.remote_addr,
                user_agent=(request.user_agent.string or "")[:500],
            )
        )
        db.session.commit()

        flash("Organization settings updated successfully.", "success")
        return redirect(url_for("settings.organization_settings"))

    return render_template(
        "app/settings_org.html",
        form=form,
        branding=branding_settings,
        sso=sso_settings,
        plan=db.session.get(Plan, g.org.plan_id) if g.org.plan_id else None,
    )


@settings_bp.route("/settings/organization", methods=["DELETE"])
@login_required
@org_required
def delete_organization():
    """Soft delete the current organization after strict confirmation."""

    membership = _current_org_membership()
    if membership is None or membership.role != "owner":
        return error_response("Only the organization owner can delete this organization.", 403)

    payload = request.get_json(silent=True) or {}
    confirmation_slug = str(payload.get("confirmation_slug") or "").strip().lower()

    if confirmation_slug != str(g.org.slug).strip().lower():
        return error_response("Please type your organization slug to confirm deletion.", 400)

    try:
        try:
            billing_service.cancel_subscription(
                str(g.org.id),
                reason="Organization deletion requested by owner",
            )
        except BillingServiceError as exc:
            if "No active subscription found" not in str(exc):
                current_app.logger.warning("Subscription cancellation during org delete failed: %s", exc)

        g.org.is_deleted = True
        g.org.deleted_at = _utcnow()
        g.org.slug = f"deleted-{str(g.org.id)[:8]}"

        db.session.add(
            AuditLog(
                org_id=g.org.id,
                user_id=current_user.id,
                action="organization.deleted",
                resource_type="organization",
                resource_id=str(g.org.id),
                ip_address=request.remote_addr,
                user_agent=(request.user_agent.string or "")[:500],
            )
        )

        db.session.commit()
    except Exception as exc:  # pylint: disable=broad-except
        db.session.rollback()
        current_app.logger.error("Failed to delete organization %s: %s", g.org.id, exc)
        return error_response("Unable to delete organization right now.", 500)

    logout_user()
    session.clear()
    return success_response({"deleted": True, "redirect": "/"})


@settings_bp.get("/settings/billing")
@login_required
@org_required
def billing_settings():
    """Render billing and subscription settings page."""

    try:
        billing_summary = billing_service.get_billing_summary(str(g.org.id))
    except BillingServiceError as exc:
        current_app.logger.error("Billing summary load failed for org %s: %s", g.org.id, exc)
        billing_summary = {
            "current_plan": db.session.get(Plan, g.org.plan_id) if g.org.plan_id else None,
            "current_subscription": None,
            "invoices": [],
            "next_billing_date": None,
            "days_until_renewal": None,
            "is_cancelled": False,
            "total_spent_inr": 0,
        }

    plans = (
        Plan.query.filter_by(is_active=True)
        .order_by(
            case((Plan.price_monthly_inr < 0, 1), else_=0),
            Plan.price_monthly_inr.asc(),
        )
        .all()
    )

    month_start = _utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    tasks_used_this_month = (
        UsageRecord.query.filter(
            UsageRecord.org_id == g.org.id,
            UsageRecord.usage_type == "task_run",
            UsageRecord.recorded_at >= month_start,
        ).count()
    )

    return render_template(
        "app/settings_billing.html",
        billing_summary=billing_summary,
        plans=plans,
        tasks_used_this_month=tasks_used_this_month,
        razorpay_key_id=os.environ.get("RAZORPAY_KEY_ID", ""),
    )


@settings_bp.route("/settings/api-keys", methods=["GET", "POST"])
@login_required
@org_required
def api_keys_settings():
    """Render API key settings and create new keys."""

    plan = db.session.get(Plan, g.org.plan_id) if g.org.plan_id else None
    can_create_api_keys = _plan_has_api_access(plan)

    if request.method == "GET":
        api_keys = (
            ApiKey.query.filter_by(org_id=g.org.id, user_id=current_user.id)
            .order_by(ApiKey.created_at.desc())
            .all()
        )
        return render_template(
            "app/settings_api_keys.html",
            api_keys=api_keys,
            can_create_api_keys=can_create_api_keys,
            current_plan=plan,
        )

    if not can_create_api_keys:
        return error_response("API access requires Pro plan or higher.", 403)

    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict(flat=False)
    payload = payload if isinstance(payload, dict) else {}

    label_raw = payload.get("label")
    label = label_raw[0] if isinstance(label_raw, list) else label_raw
    label = str(label or "").strip()
    if not label or len(label) > 255:
        return error_response("Label is required and must be at most 255 characters.", 400)

    scopes_raw = payload.get("scopes")
    scopes = _serialize_scopes(scopes_raw)
    if not scopes and not request.is_json:
        scopes = [str(item).strip().lower() for item in request.form.getlist("scopes") if str(item).strip()]

    allowed_scopes = {"read", "write", "admin"}
    if not scopes or any(scope not in allowed_scopes for scope in scopes):
        return error_response("Please choose valid API key scopes.", 400)

    if "admin" in scopes and (plan is None or plan.slug != "enterprise"):
        return error_response("Admin API scope is available on Enterprise plans only.", 403)

    expires_value = payload.get("expires_at")
    if isinstance(expires_value, list):
        expires_value = expires_value[0]
    if not expires_value:
        expires_value = request.form.get("expires_at")

    expires_at = None
    if expires_value:
        expires_str = str(expires_value).strip()
        try:
            expires_at = datetime.strptime(expires_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return error_response("Expiry date must be in YYYY-MM-DD format.", 400)

    raw_key = f"agf_{secrets.token_urlsafe(40)}"
    key_hash = bcrypt.hashpw(raw_key.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")
    key_prefix = raw_key[:8]

    api_key = ApiKey(
        org_id=g.org.id,
        user_id=current_user.id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        label=label,
        scopes_json=sorted(set(scopes)),
        expires_at=expires_at,
        is_active=True,
    )

    db.session.add(api_key)
    db.session.add(
        AuditLog(
            org_id=g.org.id,
            user_id=current_user.id,
            action="api_key.created",
            resource_type="api_key",
            resource_id=str(api_key.id),
            ip_address=request.remote_addr,
            user_agent=(request.user_agent.string or "")[:500],
            extra_json={"label": label, "key_prefix": key_prefix, "scopes": sorted(set(scopes))},
        )
    )
    db.session.commit()

    return success_response(
        {
            "raw_key": raw_key,
            "message": "Save this key - it will only be shown once!",
            "key_id": str(api_key.id),
            "key_prefix": key_prefix,
            "label": label,
        }
    )


@settings_bp.delete("/settings/api-keys/<key_id>")
@login_required
@org_required
def revoke_api_key(key_id: str):
    """Soft revoke an API key owned by current user."""

    if not validate_uuid(key_id):
        return error_response("Invalid API key ID.", 400)

    api_key = ApiKey.query.filter_by(id=UUID(key_id), user_id=current_user.id, org_id=g.org.id).first()
    if api_key is None:
        return error_response("API key not found.", 404)

    api_key.is_active = False
    db.session.add(
        AuditLog(
            org_id=g.org.id,
            user_id=current_user.id,
            action="api_key.revoked",
            resource_type="api_key",
            resource_id=str(api_key.id),
            ip_address=request.remote_addr,
            user_agent=(request.user_agent.string or "")[:500],
            extra_json={"key_prefix": api_key.key_prefix, "label": api_key.label},
        )
    )
    db.session.commit()

    return success_response({"revoked": True})
