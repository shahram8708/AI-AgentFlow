"""Workflow and schedule forms."""

from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from app.utils.validators import validate_cron_expression


DEFAULT_DAILY_CRON = "0 9 * * *"
DEFAULT_WEEKLY_CRON = "0 9 * * 1"
DEFAULT_MONTHLY_CRON = "0 9 1 * *"


COMMON_TIMEZONE_CHOICES: list[tuple[str, str]] = [
    ("Asia/Kolkata", "IST - Asia/Kolkata"),
    ("UTC", "UTC"),
    ("America/New_York", "EST/EDT - America/New_York"),
    ("America/Chicago", "CST/CDT - America/Chicago"),
    ("America/Denver", "MST/MDT - America/Denver"),
    ("America/Los_Angeles", "PST/PDT - America/Los_Angeles"),
    ("Europe/London", "GMT/BST - Europe/London"),
    ("Europe/Paris", "CET/CEST - Europe/Paris"),
    ("Europe/Berlin", "CET/CEST - Europe/Berlin"),
    ("Asia/Tokyo", "JST - Asia/Tokyo"),
    ("Asia/Singapore", "SGT - Asia/Singapore"),
    ("Asia/Dubai", "GST - Asia/Dubai"),
    ("Australia/Sydney", "AEST/AEDT - Australia/Sydney"),
]


class WorkflowForm(FlaskForm):
    """Workflow creation and editing form."""

    name = StringField("Name", validators=[DataRequired(), Length(min=2, max=255)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=1000)])
    trigger_type = SelectField(
        "Trigger Type",
        choices=[
            ("manual", "Manual"),
            ("scheduled", "Scheduled"),
            ("webhook", "Webhook"),
        ],
        default="manual",
    )
    project_id = SelectField("Project", choices=[("", "No project")], coerce=str, validators=[Optional()])
    is_public = BooleanField("Share with team members", default=False)
    tags = StringField(
        "Tags",
        validators=[Optional(), Length(max=500)],
        description="Comma-separated tags",
    )
    submit = SubmitField("Save Workflow")


class WorkflowRunForm(FlaskForm):
    """Workflow run options form."""

    parameter_overrides = TextAreaField(
        "Override Parameters (JSON)",
        validators=[Optional(), Length(max=5000)],
        description="Optionally override workflow step parameters as JSON",
    )
    project_id = SelectField("Project", choices=[("", "Use workflow default")], coerce=str, validators=[Optional()])
    priority = SelectField(
        "Priority",
        choices=[
            ("default", "Default"),
            ("high", "High"),
            ("low", "Low"),
        ],
        default="default",
    )
    submit = SubmitField("Run Workflow")


class ScheduleForm(FlaskForm):
    """Schedule creation and update form."""

    name = StringField("Name", validators=[DataRequired(), Length(min=2, max=255)])
    workflow_id = SelectField("Workflow", choices=[], coerce=str, validators=[DataRequired()])
    schedule_type = SelectField(
        "Schedule Type",
        choices=[
            ("daily", "Daily"),
            ("weekly", "Weekly"),
            ("monthly", "Monthly"),
            ("custom", "Custom"),
        ],
        default="daily",
    )
    cron_expression = StringField(
        "Cron Expression",
        validators=[Optional(), Length(max=100)],
    )
    timezone = SelectField(
        "Timezone",
        choices=COMMON_TIMEZONE_CHOICES,
        default="Asia/Kolkata",
        validators=[DataRequired()],
    )
    notify_on_completion = BooleanField("Notify me when this runs", default=True)
    submit = SubmitField("Create Schedule")

    def validate_cron_expression_field(self, field: StringField) -> None:
        """Validate or auto-generate cron expression based on selected schedule type."""

        schedule_type = (self.schedule_type.data or "daily").strip().lower()
        cron_value = (field.data or "").strip()

        if schedule_type == "custom":
            if not cron_value:
                raise ValidationError("Cron expression is required for custom schedules.")
            if not validate_cron_expression(cron_value):
                raise ValidationError("Please enter a valid 5-field cron expression.")
            field.data = cron_value
            return

        # Allow explicit generated cron from the visual builder when valid.
        if cron_value and validate_cron_expression(cron_value):
            field.data = cron_value
            return

        if schedule_type == "weekly":
            field.data = DEFAULT_WEEKLY_CRON
        elif schedule_type == "monthly":
            field.data = DEFAULT_MONTHLY_CRON
        else:
            field.data = DEFAULT_DAILY_CRON

    def validate_cron_expression(self, field: StringField) -> None:
        """WTForms hook for cron_expression field validation."""

        self.validate_cron_expression_field(field)
