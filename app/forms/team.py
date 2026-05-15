"""Team management forms."""

from __future__ import annotations

from flask import g
from flask_wtf import FlaskForm
from sqlalchemy import func
from wtforms import HiddenField, SelectField, SubmitField
from wtforms.fields import EmailField
from wtforms.validators import DataRequired, Email, Length, ValidationError

from app.models import OrganizationMember, User


ROLE_CHOICES = [
    (
        "admin",
        "Admin - Can manage tasks, workflows, and settings",
    ),
    (
        "member",
        "Member - Can create and run tasks and workflows",
    ),
    (
        "viewer",
        "Viewer - Read-only access to tasks and outputs",
    ),
]


class InviteMemberForm(FlaskForm):
    """Team member invitation form."""

    email = EmailField(
        "Email",
        validators=[DataRequired(), Email(), Length(max=255)],
    )
    role = SelectField(
        "Role",
        choices=ROLE_CHOICES,
        default="member",
        validators=[DataRequired()],
    )
    submit = SubmitField("Send Invitation")

    def validate_email(self, field: EmailField) -> None:
        """Prevent inviting emails already in the current organization."""

        normalized = (field.data or "").strip().lower()
        field.data = normalized

        organization = getattr(g, "org", None)
        if organization is None:
            return

        existing_member = (
            OrganizationMember.query.join(User, OrganizationMember.user_id == User.id)
            .filter(
                OrganizationMember.org_id == organization.id,
                func.lower(User.email) == normalized,
            )
            .first()
        )
        if existing_member:
            raise ValidationError("This email is already a member of your organization.")


class UpdateRoleForm(FlaskForm):
    """Role update form for existing organization members."""

    role = SelectField(
        "Role",
        choices=ROLE_CHOICES,
        validators=[DataRequired()],
    )
    member_id = HiddenField("Member ID", validators=[DataRequired()])
    submit = SubmitField("Update Role")
