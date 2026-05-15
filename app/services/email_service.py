"""Transactional email service for account and platform workflows."""

from __future__ import annotations

import re
from typing import Any

from flask import current_app, render_template
from flask_mail import Message

from app.extensions import mail


class EmailService:
    """Service for transactional email delivery through Flask Mail.

    Parameters:
        app: Optional Flask application instance.

    Side effects:
        Sends outbound emails through configured SMTP provider.
    """

    def __init__(self, app: Any | None = None) -> None:
        self.mail = None
        if app is not None:
            self.init_app(app)

    def init_app(self, app: Any) -> None:
        """Initialize service against Flask app using shared mail extension.

        Parameters:
            app: Flask application instance.

        Returns:
            None.
        """

        _ = app
        self.mail = mail

    def _html_to_text(self, html: str) -> str:
        """Convert HTML content into a plain text fallback body.

        Parameters:
            html: HTML email content.

        Returns:
            Plain text representation suitable for text only mail clients.
        """

        text = re.sub(r"<style[\\s\\S]*?</style>", "", html, flags=re.IGNORECASE)
        text = re.sub(r"<script[\\s\\S]*?</script>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\\s+", " ", text).strip()
        return text

    def send_generic_email(
        self,
        to_email: str,
        subject: str,
        template_name: str,
        context: dict[str, Any],
    ) -> bool:
        """Send any templated transactional email.

        Parameters:
            to_email: Recipient email address.
            subject: Email subject line.
            template_name: Jinja template path.
            context: Template context dictionary.

        Returns:
            True when email send succeeds, otherwise False.

        Side effects:
            Sends an email and logs errors on failure.
        """

        try:
            html_body = render_template(template_name, **context)
            text_body = self._html_to_text(html_body)
            sender = current_app.config.get("MAIL_DEFAULT_SENDER")
            message = Message(
                subject=subject,
                recipients=[to_email],
                body=text_body,
                html=html_body,
                sender=sender,
            )
            mail.send(message)
            return True
        except Exception as exc:
            current_app.logger.error(
                "Email send failed for template %s to %s: %s",
                template_name,
                to_email,
                exc,
            )
            return False

    def send_verification_email(
        self,
        user: Any,
        verification_token: str,
        base_url: str,
    ) -> bool:
        """Send a verification email for a new local account.

        Parameters:
            user: User model instance.
            verification_token: Generated verification token.
            base_url: Absolute host URL from incoming request.

        Returns:
            True on send success, else False.
        """

        normalized_base = (base_url or "").rstrip("/")
        verification_link = f"{normalized_base}/auth/verify/{verification_token}"
        context = {
            "first_name": user.first_name,
            "verification_link": verification_link,
            "expiry_hours": 24,
            "platform_name": "AgentFlow",
        }
        return self.send_generic_email(
            to_email=user.email,
            subject="Verify your AgentFlow account - action required",
            template_name="emails/verify_email.html",
            context=context,
        )

    def send_password_reset_email(
        self,
        user: Any,
        reset_token: str,
        base_url: str,
    ) -> bool:
        """Send a password reset email with a single use token.

        Parameters:
            user: User model instance.
            reset_token: Generated reset token.
            base_url: Absolute host URL from incoming request.

        Returns:
            True on send success, else False.
        """

        normalized_base = (base_url or "").rstrip("/")
        reset_link = f"{normalized_base}/auth/reset-password/{reset_token}"
        context = {
            "first_name": user.first_name,
            "reset_link": reset_link,
            "expiry_hours": 1,
            "platform_name": "AgentFlow",
            "security_note": (
                "If you didn't request this, ignore this email. "
                "Your password will not change."
            ),
        }
        return self.send_generic_email(
            to_email=user.email,
            subject="Reset your AgentFlow password",
            template_name="emails/reset_password.html",
            context=context,
        )

    def send_welcome_email(self, user: Any) -> bool:
        """Send welcome onboarding email after successful verification.

        Parameters:
            user: User model instance.

        Returns:
            True on send success, else False.
        """

        frontend_url = (current_app.config.get("FRONTEND_URL") or "").rstrip("/")
        dashboard_link = f"{frontend_url}/dashboard" if frontend_url else "/dashboard"
        context = {
            "first_name": user.first_name,
            "dashboard_link": dashboard_link,
            "starter_tasks": [
                "Launch your first research task",
                "Save a workflow for recurring tasks",
                "Explore 481 automation templates",
            ],
            "support_email": "support@agentflow.ai",
            "platform_name": "AgentFlow",
        }
        return self.send_generic_email(
            to_email=user.email,
            subject=f"Welcome to AgentFlow, {user.first_name}! 🚀",
            template_name="emails/welcome.html",
            context=context,
        )

    def send_mfa_backup_codes_email(self, user: Any, backup_codes: list[str]) -> bool:
        """Send generated MFA backup codes to user email.

        Parameters:
            user: User model instance.
            backup_codes: Plain text backup codes.

        Returns:
            True on send success, else False.
        """

        context = {
            "first_name": user.first_name,
            "backup_codes": backup_codes,
            "platform_name": "AgentFlow",
            "security_warning": (
                "Store these codes in a safe place. "
                "Each code can only be used once."
            ),
        }
        return self.send_generic_email(
            to_email=user.email,
            subject="Your AgentFlow 2FA backup codes - save these securely",
            template_name="emails/mfa_backup_codes.html",
            context=context,
        )

    def send_contact_notification(self, contact_data: dict[str, Any]) -> bool:
        """Send internal email notification for contact form submissions.

        Parameters:
            contact_data: Contact payload fields.

        Returns:
            True on send success, else False.
        """

        try:
            to_email = current_app.config.get("MAIL_DEFAULT_SENDER")
            recipient = to_email[1] if isinstance(to_email, (list, tuple)) else to_email
            subject = contact_data.get("subject", "General")
            body_lines = [
                "New contact form submission",
                "",
                f"Name: {contact_data.get('name', 'N/A')}",
                f"Email: {contact_data.get('email', 'N/A')}",
                f"Company: {contact_data.get('company', 'N/A')}",
                f"Subject: {subject}",
                "",
                "Message:",
                str(contact_data.get("message", "")),
            ]
            body = "\n".join(body_lines)
            message = Message(
                subject=f"New contact form submission: {subject}",
                recipients=[str(recipient)],
                body=body,
                sender=current_app.config.get("MAIL_DEFAULT_SENDER"),
            )
            mail.send(message)
            return True
        except Exception as exc:
            current_app.logger.error(
                "Failed to send contact notification email: %s",
                exc,
            )
            return False

    def send_team_invitation_email(
        self,
        invited_user: Any,
        invited_by_user: Any,
        org: Any,
        role: str,
    ) -> bool:
        """Send team invitation email with secure invitation token URL."""

        import hashlib
        import hmac

        from app.models import OrganizationMember

        member = OrganizationMember.query.filter_by(org_id=org.id, user_id=invited_user.id).first()
        if member is None or member.invited_at is None:
            current_app.logger.error(
                "Cannot send team invitation email, membership not found for org %s user %s",
                org.id,
                invited_user.id,
            )
            return False

        token_data = f"{str(member.id)}{member.invited_at.isoformat()}"
        invitation_token = hmac.new(
            current_app.config["SECRET_KEY"].encode(),
            token_data.encode(),
            hashlib.sha256,
        ).hexdigest()[:32]

        base_url = current_app.config.get("FRONTEND_URL", "http://localhost:5000").rstrip("/")
        invitation_url = f"{base_url}/team/accept-invitation/{invitation_token}"

        context = {
            "invited_name": invited_user.first_name if invited_user.first_name else "there",
            "invited_by_name": invited_by_user.get_full_name(),
            "invited_by_role": "team member",
            "org_name": org.name,
            "role": role,
            "invitation_url": invitation_url,
            "expires_in_days": 7,
            "requires_signup": not bool(invited_user.is_verified and invited_user.is_active),
        }

        return self.send_generic_email(
            invited_user.email,
            f"You're invited to join {org.name} on AgentFlow",
            "emails/team_invitation.html",
            context,
        )
