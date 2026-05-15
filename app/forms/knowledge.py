"""Knowledge base forms."""

from __future__ import annotations

from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from wtforms import SelectField, StringField, SubmitField, TextAreaField
from wtforms.fields import URLField
from wtforms.validators import Length, Optional


class KnowledgeEntryForm(FlaskForm):
    """Knowledge base entry form for file, URL, and text sources."""

    source_type = SelectField(
        "Source Type",
        choices=[("file", "File"), ("url", "URL"), ("text", "Text")],
        default="text",
    )
    title = StringField("Title", validators=[Optional(), Length(max=500)])
    content = TextAreaField("Content", validators=[Optional(), Length(max=50000)])
    url = URLField("URL", validators=[Optional(), Length(max=500)])
    project_id = SelectField(
        "Project",
        choices=[("", "No project")],
        coerce=str,
        validators=[Optional()],
    )
    file = FileField("File", validators=[Optional()])
    submit = SubmitField("Save")

    def validate_on_submit_custom(self) -> bool:
        """Apply source type specific validation after base validators."""

        if not super().validate():
            return False

        source_type = (self.source_type.data or "text").strip().lower()

        if source_type == "text":
            title = (self.title.data or "").strip()
            content = (self.content.data or "").strip()
            if not title:
                self.title.errors.append("Title is required for text entries.")
            if not content:
                self.content.errors.append("Content is required for text entries.")
            return bool(title and content)

        if source_type == "url":
            title = (self.title.data or "").strip()
            url = (self.url.data or "").strip()
            if not title:
                self.title.errors.append("Title is required for URL entries.")
            if not url:
                self.url.errors.append("URL is required for URL entries.")
            return bool(title and url)

        if source_type == "file":
            return True

        self.source_type.errors.append("Invalid source type selected.")
        return False
