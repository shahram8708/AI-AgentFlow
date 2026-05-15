"""Public website forms."""

from flask_wtf import FlaskForm
from wtforms import EmailField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional


class ContactForm(FlaskForm):
    """Contact form for public inquiries."""

    name = StringField("Name", validators=[DataRequired(), Length(min=2, max=120)])
    email = EmailField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    company = StringField("Company", validators=[Optional(), Length(max=255)])
    subject = SelectField(
        "Subject",
        validators=[DataRequired()],
        choices=[
            ("General Inquiry", "General Inquiry"),
            ("Sales", "Sales"),
            ("Technical Support", "Technical Support"),
            ("Partnership", "Partnership"),
            ("Press", "Press"),
        ],
    )
    message = TextAreaField(
        "Message",
        validators=[DataRequired(), Length(min=20, max=5000)],
    )
    submit = SubmitField("Send Message")


class EnterpriseInquiryForm(FlaskForm):
    """Pricing page enterprise inquiry form."""

    company_name = StringField(
        "Company Name",
        validators=[DataRequired(), Length(min=2, max=255)],
    )
    name = StringField("Name", validators=[DataRequired(), Length(min=2, max=120)])
    work_email = EmailField(
        "Work Email",
        validators=[DataRequired(), Email(), Length(max=255)],
    )
    team_size = SelectField(
        "Team Size",
        validators=[DataRequired()],
        choices=[
            ("1-10", "1-10"),
            ("11-50", "11-50"),
            ("51-200", "51-200"),
            ("201-1000", "201-1000"),
            ("1000+", "1000+"),
        ],
    )
    message = TextAreaField(
        "Message",
        validators=[DataRequired(), Length(min=20, max=5000)],
    )
    submit = SubmitField("Contact Sales")
