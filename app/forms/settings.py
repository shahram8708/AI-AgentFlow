"""Settings forms."""

from __future__ import annotations

from flask import g
from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
)
from wtforms.validators import (
    DataRequired,
    EqualTo,
    Length,
    Regexp,
    ValidationError,
)

from app.forms.auth import _validate_password_rules


TIMEZONE_CHOICES = [
    ("Asia/Kolkata", "Asia/Kolkata (IST)"),
    ("Asia/Dubai", "Asia/Dubai (GST)"),
    ("Asia/Singapore", "Asia/Singapore (SGT)"),
    ("Asia/Tokyo", "Asia/Tokyo (JST)"),
    ("Asia/Bangkok", "Asia/Bangkok (ICT)"),
    ("Europe/London", "Europe/London (GMT/BST)"),
    ("Europe/Berlin", "Europe/Berlin (CET/CEST)"),
    ("Europe/Paris", "Europe/Paris (CET/CEST)"),
    ("Europe/Amsterdam", "Europe/Amsterdam (CET/CEST)"),
    ("Europe/Dublin", "Europe/Dublin (GMT/IST)"),
    ("America/New_York", "America/New York (ET)"),
    ("America/Chicago", "America/Chicago (CT)"),
    ("America/Denver", "America/Denver (MT)"),
    ("America/Los_Angeles", "America/Los Angeles (PT)"),
    ("America/Toronto", "America/Toronto (ET)"),
    ("America/Sao_Paulo", "America/Sao Paulo (BRT)"),
    ("Australia/Sydney", "Australia/Sydney (AEST/AEDT)"),
    ("Australia/Perth", "Australia/Perth (AWST)"),
    ("Africa/Johannesburg", "Africa/Johannesburg (SAST)"),
    ("UTC", "UTC"),
]


class AccountSettingsForm(FlaskForm):
    """User account profile and notification settings form."""

    first_name = StringField("First Name", validators=[DataRequired(), Length(min=2, max=100)])
    last_name = StringField("Last Name", validators=[DataRequired(), Length(min=2, max=100)])
    timezone = SelectField(
        "Timezone",
        choices=TIMEZONE_CHOICES,
        default="Asia/Kolkata",
        validators=[DataRequired()],
    )
    email_notifications_tasks = BooleanField(
        "Task completions and failures",
        default=True,
    )
    email_notifications_billing = BooleanField(
        "Billing and subscription updates",
        default=True,
    )
    email_notifications_team = BooleanField(
        "Team activity and invitations",
        default=True,
    )
    email_notifications_weekly_digest = BooleanField(
        "Weekly usage digest",
        default=True,
    )
    submit = SubmitField("Save Changes")


class ChangePasswordForm(FlaskForm):
    """Change password form with strong password policy."""

    current_password = PasswordField("Current Password", validators=[DataRequired()])
    new_password = PasswordField(
        "New Password",
        validators=[DataRequired(), Length(min=8, max=128)],
    )
    confirm_new_password = PasswordField(
        "Confirm New Password",
        validators=[DataRequired(), EqualTo("new_password")],
    )
    submit = SubmitField("Change Password")

    def validate_new_password(self, field: PasswordField) -> None:
        """Apply the same password strength checks as registration."""

        _validate_password_rules(field.data or "")


class OrgSettingsForm(FlaskForm):
    """Organization profile and slug settings form."""

    name = StringField("Organization Name", validators=[DataRequired(), Length(min=2, max=255)])
    slug = StringField(
        "Workspace Slug",
        validators=[
            DataRequired(),
            Length(min=2, max=100),
            Regexp(
                r"^[a-z0-9-]+$",
                message="Slug can only contain lowercase letters, numbers, and hyphens",
            ),
        ],
    )
    timezone = SelectField(
        "Timezone",
        choices=TIMEZONE_CHOICES,
        default="Asia/Kolkata",
        validators=[DataRequired()],
    )
    submit = SubmitField("Save Organization Settings")

    def validate_slug(self, field: StringField) -> None:
        """Ensure slug is globally unique except for the current organization."""

        from app.models import Organization

        candidate = (field.data or "").strip().lower()
        field.data = candidate

        query = Organization.query.filter(Organization.slug == candidate)
        current_org = getattr(g, "org", None)
        if current_org is not None:
            query = query.filter(Organization.id != current_org.id)

        existing = query.first()
        if existing is not None:
            raise ValidationError("This workspace slug is already in use.")


class DeleteAccountForm(FlaskForm):
    """Account deletion confirmation form."""

    password = PasswordField(
        "Enter your password to confirm",
        validators=[DataRequired()],
    )
    confirmation_text = StringField(
        "Confirmation Text",
        validators=[DataRequired(), Length(max=64)],
    )
    submit = SubmitField("Permanently Delete Account")

    def validate_confirmation_text(self, field: StringField) -> None:
        """Require exact irreversible deletion phrase."""

        if (field.data or "") != "DELETE MY ACCOUNT":
            raise ValidationError('Please type "DELETE MY ACCOUNT" exactly to continue.')


# Backward compatible aliases retained for existing imports.
ProfileSettingsForm = AccountSettingsForm
OrganizationSettingsForm = OrgSettingsForm
