"""Authentication service layer with security hardened workflows."""

from __future__ import annotations

import base64
import hmac
import io
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import bcrypt
import pyotp
import qrcode
from flask import current_app
from sqlalchemy import func

from app.extensions import db
from app.models import (
    AuditLog,
    EmailVerificationToken,
    Organization,
    OrganizationMember,
    PasswordResetToken,
    Plan,
    User,
)

DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"agentflow-dummy", bcrypt.gensalt(rounds=12))


def _utcnow() -> datetime:
    """Return current UTC datetime as a timezone aware value."""

    return datetime.now(timezone.utc)


def _to_utc(value: datetime | None) -> datetime | None:
    """Normalize datetime values to UTC for safe comparisons.

    Parameters:
        value: Datetime value that may be naive or timezone aware.

    Returns:
        UTC timezone aware datetime or None.
    """

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_safe_url(target: str | None, host: str) -> bool:
    """Validate that a redirect target remains on the same host.

    Parameters:
        target: Candidate redirect URL path or absolute URL.
        host: Current request host.

    Returns:
        True when target points to same host and is HTTP or HTTPS.
    """

    if not target:
        return False

    try:
        host_value = host.split("://", 1)[1] if "://" in host else host
        reference_url = urlparse(f"https://{host_value}")
        test_url = urlparse(urljoin(f"https://{host_value}", target))
        return test_url.scheme in {"http", "https"} and hmac.compare_digest(
            test_url.netloc,
            reference_url.netloc,
        )
    except Exception:
        return False


def write_audit_log(
    action: str,
    user_id: Any | None = None,
    org_id: Any | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    before_json: dict[str, Any] | list[Any] | None = None,
    after_json: dict[str, Any] | list[Any] | None = None,
    extra_json: dict[str, Any] | list[Any] | None = None,
) -> bool:
    """Persist an audit log entry with immediate commit.

    Parameters:
        action: Audit action name.
        user_id: Related user identifier.
        org_id: Related organization identifier.
        resource_type: Resource type for this event.
        resource_id: Resource identifier string.
        ip_address: Client IP address.
        user_agent: Browser or client user agent string.
        before_json: State snapshot before change.
        after_json: State snapshot after change.
        extra_json: Extra event metadata.

    Returns:
        True if persisted successfully, otherwise False.

    Side effects:
        Inserts into audit_logs and commits the transaction.
    """

    try:
        audit = AuditLog(
            action=action,
            user_id=user_id,
            org_id=org_id,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=ip_address,
            user_agent=user_agent,
            before_json=before_json,
            after_json=after_json,
            extra_json=extra_json,
        )
        db.session.add(audit)
        db.session.commit()
        return True
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("Failed to write audit log for %s: %s", action, exc)
        return False


class AuthService:
    """Service handling authentication, token, OAuth, and MFA workflows."""

    def generate_unique_slug(self, email: str) -> str:
        """Generate a unique organization slug based on email prefix.

        Parameters:
            email: User email address.

        Returns:
            Unique slug string up to 100 characters.
        """

        try:
            local_part = (email.split("@", 1)[0] if "@" in email else email).strip().lower()
            base_slug = re.sub(r"[^a-z0-9]+", "-", local_part).strip("-")
            if not base_slug:
                base_slug = "workspace"
            base_slug = base_slug[:50].rstrip("-")
            candidate = base_slug

            while Organization.query.filter_by(slug=candidate).first() is not None:
                suffix = str(secrets.randbelow(9000) + 1000)
                candidate = f"{base_slug[:45].rstrip('-')}-{suffix}".strip("-")

            return candidate[:100]
        except Exception as exc:
            current_app.logger.error("Failed to generate unique slug for %s: %s", email, exc)
            fallback = f"workspace-{secrets.randbelow(9000) + 1000}"
            return fallback[:100]

    def create_user(
        self,
        first_name: str,
        last_name: str,
        email: str,
        password: str | None,
        oauth_provider: str | None = None,
        oauth_id: str | None = None,
    ) -> tuple[User | None, Organization | None]:
        """Create user, organization, and owner membership records.

        Parameters:
            first_name: User first name.
            last_name: User last name.
            email: User email address.
            password: Plain password for local auth users.
            oauth_provider: OAuth provider name.
            oauth_id: Provider user identifier.

        Returns:
            Tuple of created user and organization, or (None, None) on failure.

        Side effects:
            Writes users, organizations, organization_members, and audit_logs rows.
        """

        try:
            normalized_email = (email or "").strip().lower()
            user = User(
                first_name=(first_name or "").strip(),
                last_name=(last_name or "").strip(),
                email=normalized_email,
                oauth_provider=oauth_provider,
                oauth_id=oauth_id,
                is_active=True,
                is_verified=bool(oauth_provider and oauth_id),
            )
            if password:
                user.set_password(password)

            db.session.add(user)
            db.session.flush()

            free_plan = Plan.query.filter_by(slug="free", is_active=True).first()
            organization = Organization(
                name=f"{user.first_name}'s Workspace",
                slug=self.generate_unique_slug(normalized_email),
                owner_id=user.id,
                plan_id=free_plan.id if free_plan else None,
                seats_used=1,
                settings_json={},
            )
            db.session.add(organization)
            db.session.flush()

            owner_membership = OrganizationMember(
                org_id=organization.id,
                user_id=user.id,
                role="owner",
                joined_at=_utcnow(),
            )
            db.session.add(owner_membership)

            db.session.commit()

            write_audit_log(
                action="user.registered",
                user_id=user.id,
                org_id=organization.id,
                resource_type="user",
                resource_id=str(user.id),
            )
            return user, organization
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error("Failed to create user account: %s", exc)
            return None, None

    def _find_verification_token_record(self, token: str) -> EmailVerificationToken | None:
        """Find verification token record via constant time token comparison.

        Parameters:
            token: Raw token from verification URL.

        Returns:
            Matching EmailVerificationToken record or None.
        """

        for record in EmailVerificationToken.query.order_by(
            EmailVerificationToken.created_at.desc()
        ).all():
            stored_token = record.token or ""
            if hmac.compare_digest(stored_token, token):
                return record
        return None

    def _find_reset_token_record(self, token: str) -> PasswordResetToken | None:
        """Find password reset token record via constant time token comparison.

        Parameters:
            token: Raw token from reset URL.

        Returns:
            Matching PasswordResetToken record or None.
        """

        for record in PasswordResetToken.query.order_by(
            PasswordResetToken.created_at.desc()
        ).all():
            stored_token = record.token or ""
            if hmac.compare_digest(stored_token, token):
                return record
        return None

    def get_user_for_verification_token(self, token: str) -> User | None:
        """Resolve user associated with an email verification token.

        Parameters:
            token: Raw verification token.

        Returns:
            User instance when token exists, otherwise None.
        """

        try:
            record = self._find_verification_token_record(token)
            return record.user if record else None
        except Exception as exc:
            current_app.logger.error("Failed to resolve verification token user: %s", exc)
            return None

    def generate_verification_token(self, user_id: Any) -> str | None:
        """Create a new single use email verification token.

        Parameters:
            user_id: User identifier.

        Returns:
            Token string when successful, otherwise None.

        Side effects:
            Invalidates previous unused verification tokens for the user.
        """

        try:
            (
                EmailVerificationToken.query.filter(
                    EmailVerificationToken.user_id == user_id,
                    EmailVerificationToken.used_at.is_(None),
                ).delete(synchronize_session=False)
            )

            token = secrets.token_urlsafe(32)
            token_record = EmailVerificationToken(
                user_id=user_id,
                token=token,
                expires_at=_utcnow() + timedelta(hours=24),
            )
            db.session.add(token_record)
            db.session.commit()
            return token
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error("Failed to generate email verification token: %s", exc)
            return None

    def verify_email_token(self, token: str) -> tuple[bool, str]:
        """Validate and consume an email verification token.

        Parameters:
            token: Verification token from URL.

        Returns:
            Tuple containing status flag and user facing message.

        Side effects:
            Marks token as used, verifies user email, and writes audit log.
        """

        try:
            token_record = self._find_verification_token_record(token)
            if token_record is None:
                return False, "Invalid verification link."

            if token_record.is_expired():
                return (
                    False,
                    "This verification link has expired. Please request a new one.",
                )

            if token_record.used_at is not None:
                return False, "This verification link has already been used."

            user = token_record.user
            if user is None:
                return False, "Invalid verification link."

            token_record.used_at = _utcnow()
            user.is_verified = True
            user.updated_at = _utcnow()
            db.session.commit()

            organization = self.get_user_org(user.id)
            write_audit_log(
                action="user.email_verified",
                user_id=user.id,
                org_id=organization.id if organization else None,
                resource_type="user",
                resource_id=str(user.id),
            )
            return True, "Email verified successfully!"
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error("Failed to verify email token: %s", exc)
            return False, "Unable to verify email at the moment. Please try again."

    def generate_password_reset_token(self, email: str) -> tuple[User, str] | None:
        """Generate password reset token for a matching user email.

        Parameters:
            email: Email address supplied by user.

        Returns:
            Tuple of user and raw reset token when email matches a user,
            otherwise None.

        Side effects:
            Invalidates previous unused reset tokens for the user and logs request.
        """

        try:
            normalized_email = (email or "").strip().lower()
            user = User.query.filter(func.lower(User.email) == normalized_email).first()

            if user is None:
                write_audit_log(
                    action="user.password_reset_requested",
                    extra_json={"email": normalized_email, "result": "not_found"},
                )
                return None

            (
                PasswordResetToken.query.filter(
                    PasswordResetToken.user_id == user.id,
                    PasswordResetToken.used_at.is_(None),
                ).delete(synchronize_session=False)
            )

            token = secrets.token_urlsafe(32)
            reset_token = PasswordResetToken(
                user_id=user.id,
                token=token,
                expires_at=_utcnow() + timedelta(hours=1),
            )
            db.session.add(reset_token)
            db.session.commit()

            organization = self.get_user_org(user.id)
            write_audit_log(
                action="user.password_reset_requested",
                user_id=user.id,
                org_id=organization.id if organization else None,
                resource_type="user",
                resource_id=str(user.id),
            )
            return user, token
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error("Failed to generate password reset token: %s", exc)
            return None

    def verify_reset_token(self, token: str) -> tuple[bool, User | None, str]:
        """Validate password reset token without consuming it.

        Parameters:
            token: Password reset token from URL.

        Returns:
            Tuple of validity, resolved user, and message.
        """

        try:
            token_record = self._find_reset_token_record(token)
            if token_record is None:
                return False, None, "Invalid or expired reset link."

            if token_record.used_at is not None or token_record.is_expired():
                return False, None, "Invalid or expired reset link."

            return True, token_record.user, "Valid"
        except Exception as exc:
            current_app.logger.error("Failed to verify reset token: %s", exc)
            return False, None, "Invalid or expired reset link."

    def reset_password(self, token: str, new_password: str) -> tuple[bool, str]:
        """Consume a valid reset token and update account password.

        Parameters:
            token: Password reset token.
            new_password: New plain password.

        Returns:
            Tuple of status flag and user facing message.

        Side effects:
            Updates password hash, consumes reset token, invalidates others, logs audit.
        """

        try:
            valid, user, message = self.verify_reset_token(token)
            if not valid or user is None:
                return False, message

            token_record = self._find_reset_token_record(token)
            if token_record is None:
                return False, "Invalid or expired reset link."

            user.set_password(new_password)
            token_record.used_at = _utcnow()

            PasswordResetToken.query.filter(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.id != token_record.id,
                PasswordResetToken.used_at.is_(None),
            ).update({PasswordResetToken.used_at: _utcnow()}, synchronize_session=False)

            db.session.commit()

            organization = self.get_user_org(user.id)
            write_audit_log(
                action="user.password_reset",
                user_id=user.id,
                org_id=organization.id if organization else None,
                resource_type="user",
                resource_id=str(user.id),
            )
            return True, "Password reset successfully."
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error("Failed to reset password: %s", exc)
            return False, "Unable to reset password. Please request a new link."

    def authenticate_user(self, email: str, password: str) -> tuple[User | None, str]:
        """Authenticate local credentials with timing attack safeguards.

        Parameters:
            email: User email address.
            password: Plain text password.

        Returns:
            Tuple of user or None and status code.
        """

        try:
            normalized_email = (email or "").strip().lower()
            user = User.query.filter(func.lower(User.email) == normalized_email).first()

            if user is None:
                try:
                    bcrypt.checkpw((password or "").encode("utf-8"), DUMMY_PASSWORD_HASH)
                except Exception:
                    pass
                return None, "invalid_credentials"

            if not user.is_active:
                return None, "account_disabled"

            if user.password_hash is None:
                return None, "oauth_only"

            if not user.check_password(password or ""):
                return None, "invalid_credentials"

            return user, "success"
        except Exception as exc:
            current_app.logger.error("Authentication error: %s", exc)
            return None, "invalid_credentials"

    def _generate_qr_code_base64(self, secret: str, email: str) -> str:
        """Generate base64 encoded PNG QR code for TOTP provisioning.

        Parameters:
            secret: TOTP secret.
            email: User email for provisioning URI.

        Returns:
            Base64 encoded PNG image content.
        """

        totp = pyotp.TOTP(secret)
        provisioning_uri = totp.provisioning_uri(name=email, issuer_name="AgentFlow")
        qr_image = qrcode.make(provisioning_uri)
        image_buffer = io.BytesIO()
        qr_image.save(image_buffer, format="PNG")
        return base64.b64encode(image_buffer.getvalue()).decode("ascii")

    def setup_mfa(self, user: User) -> tuple[str, list[str], str]:
        """Generate MFA secret, hashed backup codes, and QR code image.

        Parameters:
            user: User model instance.

        Returns:
            Tuple of secret, plain backup codes, and base64 QR image.

        Side effects:
            Stages mfa_secret and hashed backup codes on user object without commit.
        """

        try:
            secret = pyotp.random_base32()
            backup_codes_plaintext: list[str] = []
            backup_codes_hashed: list[str] = []

            for _ in range(10):
                raw_code = secrets.token_hex(4).upper()
                formatted_code = f"{raw_code[:4]}-{raw_code[4:]}"
                backup_codes_plaintext.append(formatted_code)
                hashed = bcrypt.hashpw(
                    formatted_code.encode("utf-8"),
                    bcrypt.gensalt(rounds=12),
                ).decode("utf-8")
                backup_codes_hashed.append(hashed)

            user.mfa_secret = secret
            user.mfa_backup_codes = backup_codes_hashed

            qr_code_base64 = self._generate_qr_code_base64(secret, user.email)
            return secret, backup_codes_plaintext, qr_code_base64
        except Exception as exc:
            current_app.logger.error("Failed to setup MFA for user %s: %s", user.id, exc)
            return "", [], ""

    def verify_totp(self, user: User, totp_code: str) -> bool:
        """Validate TOTP code against user secret with drift tolerance.

        Parameters:
            user: User model instance.
            totp_code: Six digit TOTP code.

        Returns:
            True if valid, otherwise False.
        """

        try:
            if user.mfa_secret is None:
                return False
            totp = pyotp.TOTP(user.mfa_secret)
            return bool(totp.verify((totp_code or "").strip(), valid_window=1))
        except Exception as exc:
            current_app.logger.error("Failed TOTP verification for user %s: %s", user.id, exc)
            return False

    def verify_backup_code(self, user: User, code: str) -> bool:
        """Validate and consume a single use MFA backup code.

        Parameters:
            user: User model instance.
            code: Backup code provided by user.

        Returns:
            True when code is valid and consumed, otherwise False.

        Side effects:
            Removes used backup code hash and commits update.
        """

        try:
            if not user.mfa_backup_codes:
                return False

            normalized_code = (code or "").strip().upper()
            hashes: list[str] = list(user.mfa_backup_codes or [])
            matched_index: int | None = None

            for idx, stored_hash in enumerate(hashes):
                if bcrypt.checkpw(
                    normalized_code.encode("utf-8"),
                    str(stored_hash).encode("utf-8"),
                ):
                    matched_index = idx
                    break

            if matched_index is None:
                return False

            hashes.pop(matched_index)
            user.mfa_backup_codes = hashes
            db.session.commit()
            return True
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error("Failed backup code verification for user %s: %s", user.id, exc)
            return False

    def disable_mfa(self, user: User) -> bool:
        """Disable MFA and clear all backup codes for a user.

        Parameters:
            user: User model instance.

        Returns:
            True on success, otherwise False.

        Side effects:
            Clears MFA fields, commits, and writes audit log.
        """

        try:
            user.mfa_secret = None
            user.mfa_backup_codes = None
            db.session.commit()

            organization = self.get_user_org(user.id)
            write_audit_log(
                action="user.mfa_disabled",
                user_id=user.id,
                org_id=organization.id if organization else None,
                resource_type="user",
                resource_id=str(user.id),
            )
            return True
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error("Failed to disable MFA for user %s: %s", user.id, exc)
            return False

    def handle_oauth_user(
        self,
        provider: str,
        oauth_id: str,
        email: str,
        first_name: str,
        last_name: str,
        avatar_url: str | None = None,
    ) -> User | None:
        """Resolve or create user for successful OAuth authentication.

        Parameters:
            provider: OAuth provider name.
            oauth_id: OAuth provider user identifier.
            email: OAuth email claim.
            first_name: Given name claim.
            last_name: Family name claim.
            avatar_url: Optional profile picture URL.

        Returns:
            User instance on success, otherwise None.

        Side effects:
            Creates or updates user records and writes audit log.
        """

        try:
            normalized_email = (email or "").strip().lower()
            if not normalized_email:
                return None

            user = User.query.filter_by(oauth_provider=provider, oauth_id=oauth_id).first()

            if user is None:
                existing_by_email = User.query.filter(
                    func.lower(User.email) == normalized_email
                ).first()
                if existing_by_email is not None:
                    user = existing_by_email
                    user.oauth_provider = provider
                    user.oauth_id = oauth_id
                    user.is_verified = True
                else:
                    user, _ = self.create_user(
                        first_name=first_name,
                        last_name=last_name or "",
                        email=normalized_email,
                        password=None,
                        oauth_provider=provider,
                        oauth_id=oauth_id,
                    )
                    if user is None:
                        return None

            user.last_login = _utcnow()
            if avatar_url:
                user.avatar_url = avatar_url

            db.session.commit()

            organization = self.get_user_org(user.id)
            write_audit_log(
                action="user.oauth_login",
                user_id=user.id,
                org_id=organization.id if organization else None,
                resource_type="user",
                resource_id=str(user.id),
                extra_json={"provider": provider},
            )
            return user
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error("OAuth user handling failed: %s", exc)
            return None

    def record_login(self, user: User, ip_address: str | None, user_agent: str | None) -> bool:
        """Record successful login metadata and audit event.

        Parameters:
            user: Authenticated user.
            ip_address: Source IP address.
            user_agent: Source user agent string.

        Returns:
            True on success, otherwise False.
        """

        try:
            user.last_login = _utcnow()
            db.session.commit()

            organization = self.get_user_org(user.id)
            write_audit_log(
                action="user.login",
                user_id=user.id,
                org_id=organization.id if organization else None,
                resource_type="user",
                resource_id=str(user.id),
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return True
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error("Failed to record login for user %s: %s", user.id, exc)
            return False

    def record_logout(self, user: User, ip_address: str | None) -> bool:
        """Record user logout event in audit trail.

        Parameters:
            user: User being logged out.
            ip_address: Source IP address.

        Returns:
            True on success, otherwise False.
        """

        try:
            organization = self.get_user_org(user.id)
            write_audit_log(
                action="user.logout",
                user_id=user.id,
                org_id=organization.id if organization else None,
                resource_type="user",
                resource_id=str(user.id),
                ip_address=ip_address,
            )
            return True
        except Exception as exc:
            current_app.logger.error("Failed to record logout for user %s: %s", user.id, exc)
            return False

    def get_user_org(self, user_id: Any) -> Organization | None:
        """Resolve active organization membership for a user.

        Parameters:
            user_id: User identifier.

        Returns:
            Organization when membership exists, otherwise None.
        """

        try:
            membership = (
                OrganizationMember.query.join(Organization)
                .filter(
                    OrganizationMember.user_id == user_id,
                    OrganizationMember.role.in_(["owner", "admin", "member", "viewer"]),
                    Organization.is_deleted.is_(False),
                )
                .order_by(Organization.created_at.asc())
                .first()
            )
            return membership.organization if membership else None
        except Exception as exc:
            current_app.logger.error("Failed to resolve user organization: %s", exc)
            return None
