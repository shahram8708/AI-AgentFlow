"""Project forms."""

from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class ProjectForm(FlaskForm):
    """Project create and update form."""

    name = StringField("Name", validators=[DataRequired(), Length(min=2, max=255)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=1000)])
    color = StringField(
        "Color",
        default="#1a56db",
        validators=[Optional(), Length(max=7)],
        description="Choose a color for this project",
    )
    icon = SelectField(
        "Icon",
        choices=[
            ("bi-folder", "bi-folder"),
            ("bi-briefcase", "bi-briefcase"),
            ("bi-code-slash", "bi-code-slash"),
            ("bi-graph-up", "bi-graph-up"),
            ("bi-people", "bi-people"),
            ("bi-lightning", "bi-lightning"),
            ("bi-star", "bi-star"),
            ("bi-heart", "bi-heart"),
            ("bi-globe", "bi-globe"),
            ("bi-building", "bi-building"),
        ],
        default="bi-folder",
        validators=[Optional()],
    )
    submit = SubmitField("Save Project")
