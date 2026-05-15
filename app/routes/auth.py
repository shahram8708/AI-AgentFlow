"""Authentication blueprint routes."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID

import bcrypt
from authlib.integrations.flask_client import OAuth
from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_user, logout_user
from sqlalchemy import func

from app.extensions import db, limiter
from app.forms.auth import (
    ForgotPasswordForm,
    LoginForm,
    MFASetupVerifyForm,
    MFAVerifyForm,
    RegistrationForm,
    ResendVerificationForm,
    ResetPasswordForm,
)
from app.models import Notification, OrganizationMember, User
from app.services.auth_service import AuthService, is_safe_url, write_audit_log
from app.services.email_service import EmailService
from app.utils.decorators import login_required
from app.utils.response_helpers import success_response
from app.utils.sanitizer import sanitize_html

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
oauth = OAuth()

auth_service = AuthService()
email_service = EmailService()


def init_oauth(app) -> None:
    """Initialize OAuth clients for auth blueprint routes."""

    oauth.init_app(app)
    if oauth.create_client("google") is None:
        oauth.register(
            name="google",
            client_id=os.environ.get("GOOGLE_CLIENT_ID"),
            client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )


def _sanitize_name(value: str) -> str:
    """Normalize name fields to plain text values."""

    cleaned = str(sanitize_html(value or ""))
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    return " ".join(cleaned.strip().split())


def _safe_next_url(next_url: str | None) -> str | None:
    """Return safe next URL limited to same host, otherwise None."""

    if next_url and is_safe_url(next_url, request.host):
        return next_url
    return None


def _utcnow() -> datetime:
    """Return current UTC timezone aware datetime."""

    return datetime.now(timezone.utc)


def _secret_in_groups(secret: str) -> str:
    """Format TOTP secret into human friendly groups of four."""

    compact = (secret or "").replace(" ", "")
    return " ".join(compact[i : i + 4] for i in range(0, len(compact), 4)).strip()


def _is_valid_invitation_token_for_user(token: str, user: User) -> bool:
    """Validate invitation token against pending invitations for user."""

    raw_token = (token or "").strip()
    if len(raw_token) != 32:
        return False

    secret_key = str(current_app.config.get("SECRET_KEY") or "")
    if not secret_key:
        return False

    cutoff = _utcnow() - timedelta(days=7)
    pending_memberships = (
        OrganizationMember.query.filter(
            OrganizationMember.user_id == user.id,
            OrganizationMember.joined_at.is_(None),
            OrganizationMember.invited_at.isnot(None),
            OrganizationMember.invited_at >= cutoff,
        )
        .order_by(OrganizationMember.invited_at.desc())
        .all()
    )

    for membership in pending_memberships:
        token_data = f"{str(membership.id)}{membership.invited_at.isoformat() if membership.invited_at else ''}"
        expected = hmac.new(
            secret_key.encode("utf-8"),
            token_data.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:32]
        if hmac.compare_digest(expected, raw_token):
            return True

    return False


@auth_bp.get("/register")
def register():
    """Render account registration page."""

    if current_user.is_authenticated:
        return redirect("/dashboard")

    form = RegistrationForm()
    prefill_email = (request.args.get("email") or "").strip().lower()
    if prefill_email:
        form.email.data = prefill_email
    return render_template("auth/register.html", form=form)


@auth_bp.post("/register")
@limiter.limit("10 per hour")
def register_post():
    """Handle account registration submissions."""

    if current_user.is_authenticated:
        return redirect("/dashboard")

    form = RegistrationForm()
    if not form.validate_on_submit():
        return render_template("auth/register.html", form=form), 400

    first_name = _sanitize_name(form.first_name.data)
    last_name = _sanitize_name(form.last_name.data)
    email = (form.email.data or "").strip().lower()
    password = form.password.data or ""
    invitation_token = (request.args.get("invitation_token") or "").strip()

    existing_user = User.query.filter(func.lower(User.email) == email).first()
    if existing_user is not None:
        if (
            invitation_token
            and not existing_user.is_active
            and not existing_user.is_verified
            and _is_valid_invitation_token_for_user(invitation_token, existing_user)
        ):
            existing_user.first_name = first_name
            existing_user.last_name = last_name
            existing_user.set_password(password)
            existing_user.is_active = True
            existing_user.updated_at = _utcnow()
            db.session.commit()

            verification_token = auth_service.generate_verification_token(existing_user.id)
            if verification_token:
                email_service.send_verification_email(existing_user, verification_token, request.host_url)

            flash(f"Account created! Please check {email} for a verification link.", "info")
            resend_form = ResendVerificationForm()
            resend_form.email.data = email
            return render_template("auth/check_email.html", email=email, form=resend_form)

        form.email.errors.append(
            "An account with this email already exists. Please log in instead."
        )
        return render_template("auth/register.html", form=form), 400

    user, _organization = auth_service.create_user(
        first_name=first_name,
        last_name=last_name,
        email=email,
        password=password,
    )
    if user is None:
        flash("Unable to create your account right now. Please try again.", "danger")
        return render_template("auth/register.html", form=form), 500

    verification_token = auth_service.generate_verification_token(user.id)
    if verification_token:
        email_service.send_verification_email(user, verification_token, request.host_url)

    flash(f"Account created! Please check {email} for a verification link.", "info")
    resend_form = ResendVerificationForm()
    resend_form.email.data = email
    return render_template("auth/check_email.html", email=email, form=resend_form)


@auth_bp.get("/verify/<string:token>")
def verify_email(token: str):
    """Verify account email from one time token."""

    resend_form = ResendVerificationForm()
    user = auth_service.get_user_for_verification_token(token)
    success, message = auth_service.verify_email_token(token)

    if not success:
        flash(message, "danger")
        return render_template(
            "auth/verify.html",
            success=False,
            error_message=message,
            form=resend_form,
        ), 400

    if user is not None:
        email_service.send_welcome_email(user)

    flash("Email verified! Welcome to AgentFlow.", "success")
    if current_user.is_authenticated:
        return redirect("/dashboard")
    return redirect(url_for("auth.login", verified=1))


@auth_bp.post("/verify/resend")
@limiter.limit("3 per hour")
def resend_verification():
    """Resend account verification email when requested."""

    form = ResendVerificationForm()
    if form.validate_on_submit():
        email = (form.email.data or "").strip().lower()
        user = User.query.filter(func.lower(User.email) == email).first()
        if user is not None and not user.is_verified:
            token = auth_service.generate_verification_token(user.id)
            if token:
                email_service.send_verification_email(user, token, request.host_url)

    flash("Verification email sent. Check your inbox.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.get("/login")
def login():
    """Render sign in page."""

    if current_user.is_authenticated:
        return redirect("/dashboard")

    if request.args.get("verified") == "1":
        flash("Email verified successfully! Please sign in.", "success")
    if request.args.get("oauth_error") == "1":
        flash("Google sign-in failed. Please try again or use email/password.", "danger")
    if request.args.get("rate_limited") == "1":
        flash("Too many failed attempts. Please wait 15 minutes before trying again.", "warning")

    form = LoginForm()
    captcha_hint = int(session.get("login_failures", 0)) >= 3
    return render_template(
        "auth/login.html",
        form=form,
        captcha_hint=captcha_hint,
        next_url=request.args.get("next", ""),
    )


@auth_bp.post("/login")
@limiter.limit("5 per 15 minutes", key_func=lambda: request.remote_addr or "unknown")
def login_post():
    """Authenticate credentials and establish user session."""

    if current_user.is_authenticated:
        return redirect("/dashboard")

    form = LoginForm()
    next_url = request.args.get("next") or session.get("next")

    if not form.validate_on_submit():
        captcha_hint = int(session.get("login_failures", 0)) >= 3
        return render_template(
            "auth/login.html",
            form=form,
            captcha_hint=captcha_hint,
            next_url=next_url,
        ), 400

    email = (form.email.data or "").strip().lower()
    password = form.password.data or ""

    user, status = auth_service.authenticate_user(email, password)
    if user is None:
        failures = int(session.get("login_failures", 0))
        if status == "invalid_credentials":
            failures += 1
            session["login_failures"] = failures

        write_audit_log(
            action="user.login_failed",
            ip_address=request.remote_addr,
            user_agent=request.user_agent.string,
            extra_json={"email": email, "status": status},
        )

        if status == "account_disabled":
            flash("Your account has been disabled. Contact support.", "danger")
        elif status == "oauth_only":
            flash(
                "This account uses Google Sign In. Please use the Continue with Google button.",
                "warning",
            )
        else:
            flash("Invalid email or password.", "danger")

        captcha_hint = failures >= 3
        return render_template(
            "auth/login.html",
            form=form,
            captcha_hint=captcha_hint,
            next_url=next_url,
        ), 401

    previous_last_login = user.last_login

    if user.mfa_secret:
        safe_next = _safe_next_url(next_url)
        session.clear()
        session["mfa_pending_user_id"] = str(user.id)
        session["mfa_pending_remember"] = bool(form.remember_me.data)
        if safe_next:
            session["mfa_next"] = safe_next
        return redirect(url_for("auth.mfa_verify"))

    remember_me = bool(form.remember_me.data)
    safe_next = _safe_next_url(next_url)
    session.clear()
    login_user(user, remember=remember_me, duration=timedelta(days=30))

    auth_service.record_login(user, request.remote_addr, request.user_agent.string)

    previous_dt = previous_last_login
    if previous_dt is not None:
        if previous_dt.tzinfo is None:
            previous_dt = previous_dt.replace(tzinfo=timezone.utc)
        if _utcnow() - previous_dt > timedelta(days=7):
            try:
                organization = auth_service.get_user_org(user.id)
                if organization is not None:
                    notification = Notification(
                        user_id=user.id,
                        org_id=organization.id,
                        type="account",
                        title="Welcome back",
                        message="Good to see you again. Your workspace is ready.",
                        action_url="/dashboard",
                    )
                    db.session.add(notification)
                    db.session.commit()
            except Exception as exc:
                db.session.rollback()
                current_app.logger.error("Failed to create welcome back notification: %s", exc)

    if safe_next:
        return redirect(safe_next)
    return redirect("/dashboard")


@auth_bp.get("/mfa-verify")
def mfa_verify():
    """Render second factor verification page for pending login."""

    if not session.get("mfa_pending_user_id"):
        return redirect(url_for("auth.login"))

    form = MFAVerifyForm()
    return render_template("auth/mfa_verify.html", form=form)


@auth_bp.post("/mfa-verify")
@limiter.limit("5 per 5 minutes", key_func=lambda: request.remote_addr or "unknown")
def mfa_verify_post():
    """Validate MFA TOTP or backup code and complete login."""

    pending_id = session.get("mfa_pending_user_id")
    if not pending_id:
        return redirect(url_for("auth.login"))

    try:
        user_uuid = UUID(str(pending_id))
        user = db.session.get(User, user_uuid)
    except (ValueError, TypeError):
        user = None

    if user is None:
        session.clear()
        return redirect(url_for("auth.login"))

    form = MFAVerifyForm()
    backup_code = (request.form.get("backup_code") or "").strip().upper()

    valid_form = form.validate_on_submit()
    totp_code = (form.totp_code.data or "").strip() if valid_form else ""

    mfa_passed = False
    used_backup = False

    if totp_code and auth_service.verify_totp(user, totp_code):
        mfa_passed = True
    elif backup_code and auth_service.verify_backup_code(user, backup_code):
        mfa_passed = True
        used_backup = True

    if not mfa_passed:
        organization = auth_service.get_user_org(user.id)
        write_audit_log(
            action="user.mfa_failed",
            user_id=user.id,
            org_id=organization.id if organization else None,
            resource_type="user",
            resource_id=str(user.id),
            ip_address=request.remote_addr,
            user_agent=request.user_agent.string,
        )
        flash("Invalid authentication code. Please try again.", "danger")
        return render_template("auth/mfa_verify.html", form=form), 401

    remember_me = bool(session.get("mfa_pending_remember", True))
    next_url = session.get("mfa_next")

    if used_backup:
        remaining = len(user.mfa_backup_codes or [])
        flash(f"Backup code used. You have {remaining} backup codes left.", "warning")

    session.clear()
    login_user(user, remember=remember_me, duration=timedelta(days=30))
    auth_service.record_login(user, request.remote_addr, request.user_agent.string)

    safe_next = _safe_next_url(next_url)
    if safe_next:
        return redirect(safe_next)
    return redirect("/dashboard")


@auth_bp.get("/logout")
@login_required
def logout():
    """Sign out authenticated user and destroy session data."""

    auth_service.record_logout(current_user, request.remote_addr)
    logout_user()
    session.clear()
    flash("You have been signed out.", "info")
    return redirect("/")


@auth_bp.post("/logout-all")
@login_required
def logout_all():
    """Sign out current user session and rotate session revoke marker."""

    organization = auth_service.get_user_org(current_user.id)

    try:
        preferences = dict(current_user.preferences_json or {})
        preferences["session_revoked_at"] = _utcnow().isoformat()
        current_user.preferences_json = preferences
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("Failed to persist logout-all marker for user %s: %s", current_user.id, exc)

    write_audit_log(
        action="user.logout_all",
        user_id=current_user.id,
        org_id=organization.id if organization else None,
        resource_type="user",
        resource_id=str(current_user.id),
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string,
    )

    logout_user()
    session.clear()

    if request.is_json:
        return success_response({"logged_out": True, "redirect": "/auth/login"})

    flash("Signed out successfully.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.get("/forgot-password")
def forgot_password():
    """Render forgot password page."""

    if current_user.is_authenticated:
        return redirect("/dashboard")
    form = ForgotPasswordForm()
    return render_template("auth/forgot_password.html", form=form)


@auth_bp.post("/forgot-password")
@limiter.limit("3 per hour")
def forgot_password_post():
    """Handle forgot password submissions."""

    if current_user.is_authenticated:
        return redirect("/dashboard")

    form = ForgotPasswordForm()
    if not form.validate_on_submit():
        return render_template("auth/forgot_password.html", form=form), 400

    email = (form.email.data or "").strip().lower()
    result = auth_service.generate_password_reset_token(email)

    if result:
        user, reset_token = result
        email_service.send_password_reset_email(user, reset_token, request.host_url)

    flash(
        "If an account with that email exists, you will receive a password reset link shortly.",
        "info",
    )
    return redirect(url_for("auth.login"))


@auth_bp.get("/reset-password/<string:token>")
def reset_password(token: str):
    """Render reset password form when token is valid."""

    valid, _user, message = auth_service.verify_reset_token(token)
    if not valid:
        flash(message, "danger")
        return redirect(url_for("auth.forgot_password"))

    form = ResetPasswordForm()
    return render_template("auth/reset_password.html", form=form, token=token)


@auth_bp.post("/reset-password/<string:token>")
@limiter.limit("5 per hour")
def reset_password_post(token: str):
    """Handle password reset submissions."""

    valid, _user, message = auth_service.verify_reset_token(token)
    if not valid:
        flash(message, "danger")
        return redirect(url_for("auth.forgot_password"))

    form = ResetPasswordForm()
    if not form.validate_on_submit():
        return render_template("auth/reset_password.html", form=form, token=token), 400

    success, reset_message = auth_service.reset_password(token, form.password.data or "")
    if not success:
        flash(reset_message, "danger")
        return redirect(url_for("auth.forgot_password"))

    flash("Password reset successfully. Please sign in with your new password.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.get("/oauth/google")
def oauth_google():
    """Start Google OAuth authorization flow."""

    next_url = request.args.get("next")
    safe_next = _safe_next_url(next_url)
    if safe_next:
        session["oauth_next"] = safe_next

    try:
        redirect_uri = url_for("auth.oauth_callback_google", _external=True)
        return oauth.google.authorize_redirect(redirect_uri)
    except Exception as exc:
        current_app.logger.error("Failed to start Google OAuth flow: %s", exc)
        return redirect(url_for("auth.login", oauth_error=1))


@auth_bp.get("/oauth/google/callback")
def oauth_callback_google():
    """Handle Google OAuth callback and log in resolved user account."""

    try:
        token = oauth.google.authorize_access_token()
        user_info = token.get("userinfo") or oauth.google.parse_id_token(token)
        if not user_info:
            raise ValueError("Unable to retrieve user details from Google.")

        email = (user_info.get("email") or "").strip().lower()
        first_name = (user_info.get("given_name") or "").strip() or "User"
        last_name = (user_info.get("family_name") or "").strip()
        oauth_id = (user_info.get("sub") or "").strip()
        avatar_url = user_info.get("picture")

        if not email:
            raise ValueError("Google account did not provide an email address.")
        if not oauth_id:
            raise ValueError("Google account did not provide a valid user identifier.")

        user = auth_service.handle_oauth_user(
            provider="google",
            oauth_id=oauth_id,
            email=email,
            first_name=first_name,
            last_name=last_name,
            avatar_url=avatar_url,
        )
        if user is None:
            raise ValueError("Unable to complete Google sign in.")

        next_url = session.get("oauth_next")
        safe_next = _safe_next_url(next_url)

        session.clear()
        login_user(user, remember=True, duration=timedelta(days=30))
        auth_service.record_login(user, request.remote_addr, request.user_agent.string)

        if not user.onboarding_completed:
            return redirect("/onboarding")
        if safe_next:
            return redirect(safe_next)
        return redirect("/dashboard")
    except Exception as exc:
        current_app.logger.error("Google OAuth callback failed: %s", exc)
        return render_template(
            "auth/oauth_callback.html",
            error=True,
            error_message=str(exc),
        ), 400


@auth_bp.get("/mfa/setup")
@login_required
def mfa_setup():
    """Render MFA setup page with generated secret and QR code."""

    if current_user.mfa_secret:
        flash("MFA is already enabled.", "info")
        return redirect("/settings/account")

    secret, backup_codes, qr_code = auth_service.setup_mfa(current_user)
    if not secret or not backup_codes or not qr_code:
        flash("Unable to start MFA setup right now. Please try again.", "danger")
        return redirect("/settings/account")

    session["mfa_setup_secret"] = secret
    session["mfa_setup_backup_codes"] = backup_codes

    form = MFASetupVerifyForm()
    return render_template(
        "auth/mfa_setup.html",
        form=form,
        qr_code=qr_code,
        secret=secret,
        secret_grouped=_secret_in_groups(secret),
    )


@auth_bp.post("/mfa/setup")
@login_required
def mfa_setup_post():
    """Verify MFA setup code then persist secret and hashed backup codes."""

    secret = session.get("mfa_setup_secret")
    backup_codes_plain = session.get("mfa_setup_backup_codes") or []

    if not secret or not backup_codes_plain:
        flash("Your MFA setup session expired. Please start again.", "warning")
        return redirect(url_for("auth.mfa_setup"))

    form = MFASetupVerifyForm()
    qr_code = auth_service._generate_qr_code_base64(secret, current_user.email)

    if not form.validate_on_submit():
        return render_template(
            "auth/mfa_setup.html",
            form=form,
            qr_code=qr_code,
            secret=secret,
            secret_grouped=_secret_in_groups(secret),
        ), 400

    original_secret = current_user.mfa_secret
    current_user.mfa_secret = secret

    code = (form.totp_code.data or "").strip()
    if not auth_service.verify_totp(current_user, code):
        current_user.mfa_secret = original_secret
        flash("Invalid code. Please try again.", "danger")
        return render_template(
            "auth/mfa_setup.html",
            form=form,
            qr_code=qr_code,
            secret=secret,
            secret_grouped=_secret_in_groups(secret),
        ), 400

    try:
        backup_hashes = [
            bcrypt.hashpw(code_item.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
            for code_item in backup_codes_plain
        ]
        current_user.mfa_secret = secret
        current_user.mfa_backup_codes = backup_hashes
        db.session.commit()

        organization = auth_service.get_user_org(current_user.id)
        write_audit_log(
            action="user.mfa_enabled",
            user_id=current_user.id,
            org_id=organization.id if organization else None,
            resource_type="user",
            resource_id=str(current_user.id),
            ip_address=request.remote_addr,
            user_agent=request.user_agent.string,
        )

        email_service.send_mfa_backup_codes_email(current_user, list(backup_codes_plain))

        session.pop("mfa_setup_secret", None)
        session.pop("mfa_setup_backup_codes", None)

        flash(
            "Two factor authentication enabled! Backup codes have been sent to your email.",
            "success",
        )
        return redirect("/settings/account")
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("Failed to persist MFA setup: %s", exc)
        flash("Unable to enable two factor authentication right now.", "danger")
        return render_template(
            "auth/mfa_setup.html",
            form=form,
            qr_code=qr_code,
            secret=secret,
            secret_grouped=_secret_in_groups(secret),
        ), 500


@auth_bp.post("/mfa/disable")
@login_required
def mfa_disable():
    """Disable MFA after confirming current account password."""

    current_password = request.form.get("current_password", "")
    if not current_user.check_password(current_password):
        flash("Incorrect password. Two factor authentication was not disabled.", "danger")
        return redirect("/settings/account")

    success = auth_service.disable_mfa(current_user)
    if success:
        flash("Two factor authentication has been disabled.", "success")
    else:
        flash("Unable to disable two factor authentication right now.", "danger")

    session.pop("mfa_setup_secret", None)
    session.pop("mfa_setup_backup_codes", None)
    return redirect("/settings/account")
