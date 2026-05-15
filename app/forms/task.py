"""Task forms."""

from flask_wtf import FlaskForm
from wtforms import IntegerField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional


class TaskLaunchForm(FlaskForm):
    """Form to launch automation tasks."""

    task_type = StringField("Task type", validators=[DataRequired(), Length(max=100)])
    task_name = StringField("Task name", validators=[Optional(), Length(max=255)])
    input_json = TextAreaField("Input JSON", validators=[DataRequired()])
    priority = SelectField(
        "Priority",
        choices=[("high", "High"), ("default", "Default"), ("low", "Low")],
        default="default",
    )
    timeout_seconds = IntegerField(
        "Timeout (seconds)",
        validators=[DataRequired(), NumberRange(min=30, max=3600)],
        default=300,
    )
    submit = SubmitField("Run task")
