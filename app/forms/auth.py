"""Authentication and account security forms."""

from __future__ import annotations

import re

from flask_wtf import FlaskForm
from sqlalchemy import func
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.fields import EmailField
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    Length,
    Regexp,
    ValidationError,
)

from app.models import User

SPECIAL_CHARACTERS = r"!@#$%^&*()_+-=[]{}|;:,.<>?"


def _validate_password_rules(password: str) -> None:
    """Validate strict password requirements for account security."""

    if not re.search(r"[A-Z]", password):
        raise ValidationError("Password must include at least one uppercase letter.")
    if not re.search(r"[a-z]", password):
        raise ValidationError("Password must include at least one lowercase letter.")
    if not re.search(r"\d", password):
        raise ValidationError("Password must include at least one digit.")
    if not re.search(rf"[{re.escape(SPECIAL_CHARACTERS)}]", password):
        raise ValidationError(
            "Password must include at least one special character "
            "from !@#$%^&*()_+-=[]{}|;:,.<>?."
        )


class RegistrationForm(FlaskForm):
    """Account creation form with strict server side validation."""

    first_name = StringField(
        "First Name",
        validators=[DataRequired(), Length(min=2, max=100)],
    )
    last_name = StringField(
        "Last Name",
        validators=[DataRequired(), Length(min=2, max=100)],
    )
    email = EmailField(
        "Email Address",
        validators=[DataRequired(), Email(), Length(max=255)],
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired(), Length(min=8, max=128)],
    )
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match")],
    )
    agree_terms = BooleanField(
        "I agree to the Terms of Service and Privacy Policy",
        validators=[DataRequired(message="You must agree to the terms to continue")],
    )
    submit = SubmitField("Create Account")

    def validate_first_name(self, field: StringField) -> None:
        """Strip whitespace and validate first name after normalization."""

        field.data = " ".join((field.data or "").strip().split())
        if len(field.data) < 2:
            raise ValidationError("First Name must be at least 2 characters long.")

    def validate_last_name(self, field: StringField) -> None:
        """Strip whitespace and validate last name after normalization."""

        field.data = " ".join((field.data or "").strip().split())
        if len(field.data) < 2:
            raise ValidationError("Last Name must be at least 2 characters long.")

    def validate_email(self, field: EmailField) -> None:
        """Prevent duplicate user registrations by email address."""

        normalized = (field.data or "").strip().lower()
        field.data = normalized
        existing = User.query.filter(func.lower(User.email) == normalized).first()
        if existing:
            raise ValidationError(
                "An account with this email already exists. Please log in instead."
            )

    def validate_password(self, field: PasswordField) -> None:
        """Apply full password strength policy."""

        _validate_password_rules(field.data or "")


class LoginForm(FlaskForm):
    """User sign in form."""

    email = EmailField("Email Address", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember_me = BooleanField("Remember me for 30 days", default=False)
    submit = SubmitField("Sign In")


class MFAVerifyForm(FlaskForm):
    """Two factor authentication verification form."""

    totp_code = StringField(
        "Authentication Code",
        validators=[
            DataRequired(),
            Length(min=6, max=6),
            Regexp(r"^\d{6}$", message="Code must be 6 digits"),
        ],
    )
    submit = SubmitField("Verify")


class ForgotPasswordForm(FlaskForm):
    """Password reset request form."""

    email = EmailField("Email Address", validators=[DataRequired(), Email()])
    submit = SubmitField("Send Reset Link")


class ResetPasswordForm(FlaskForm):
    """Password reset completion form."""

    password = PasswordField(
        "New Password",
        validators=[DataRequired(), Length(min=8, max=128)],
    )
    confirm_password = PasswordField(
        "Confirm New Password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match")],
    )
    submit = SubmitField("Reset Password")

    def validate_password(self, field: PasswordField) -> None:
        """Apply full password strength policy."""

        _validate_password_rules(field.data or "")


class MFASetupVerifyForm(FlaskForm):
    """MFA setup confirmation form."""

    totp_code = StringField(
        "Enter the 6 digit code from your authenticator app",
        validators=[
            DataRequired(),
            Length(min=6, max=6),
            Regexp(r"^\d{6}$", message="Code must be 6 digits"),
        ],
    )
    submit = SubmitField("Enable Two Factor Authentication")


class ResendVerificationForm(FlaskForm):
    """Form for requesting a fresh email verification link."""

    email = EmailField("Email Address", validators=[DataRequired(), Email()])
    submit = SubmitField("Resend Verification Email")
