"""User and token models."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID as UUIDType
from uuid import uuid4

import bcrypt
from flask_login import UserMixin
from sqlalchemy import func

from app.extensions import db, login_manager
from app.models.db_types import JSONB, UUID


class User(UserMixin, db.Model):
    """Application user account model."""

    __tablename__ = "users"
    __table_args__ = (
        db.Index("idx_users_email", "email", unique=True),
        db.Index("idx_users_role", "role"),
        db.Index("idx_users_created_at", db.text("created_at DESC")),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    email = db.Column(db.String(255), nullable=False)
    password_hash = db.Column(db.String(255), nullable=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")
    is_verified = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    last_login = db.Column(db.DateTime(timezone=True), nullable=True)
    mfa_secret = db.Column(db.String(32), nullable=True)
    mfa_backup_codes = db.Column(JSONB, nullable=True)
    avatar_url = db.Column(db.String(500), nullable=True)
    oauth_provider = db.Column(db.String(50), nullable=True)
    oauth_id = db.Column(db.String(255), nullable=True)
    onboarding_completed = db.Column(db.Boolean, nullable=False, default=False)
    preferences_json = db.Column(JSONB, nullable=True)

    organization_memberships = db.relationship(
        "OrganizationMember",
        back_populates="user",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    owned_organizations = db.relationship(
        "Organization",
        back_populates="owner",
        lazy="dynamic",
        foreign_keys="Organization.owner_id",
    )

    def set_password(self, password: str) -> None:
        """Hash and store a user password using bcrypt."""

        self.password_hash = bcrypt.hashpw(
            password.encode("utf-8"), bcrypt.gensalt(rounds=12)
        ).decode("utf-8")

    def check_password(self, password: str) -> bool:
        """Verify a plaintext password against bcrypt hash."""

        if not self.password_hash:
            return False
        return bcrypt.checkpw(
            password.encode("utf-8"), self.password_hash.encode("utf-8")
        )

    def get_full_name(self) -> str:
        """Return full name for display."""

        return f"{self.first_name} {self.last_name}".strip()

    @property
    def is_admin(self) -> bool:
        """Return whether user has admin role."""

        return self.role == "admin"

    def __repr__(self) -> str:
        """Represent user for debugging."""

        return f"<User {self.email}>"


class EmailVerificationToken(db.Model):
    """Email verification token model."""

    __tablename__ = "email_verification_tokens"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    token = db.Column(db.String(255), nullable=False, unique=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True), nullable=True)

    user = db.relationship("User", backref=db.backref("email_tokens", lazy="dynamic"))

    def is_expired(self) -> bool:
        """Return True when token is expired."""

        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > expires_at

    def __repr__(self) -> str:
        """Represent email token for debugging."""

        return f"<EmailVerificationToken {self.user_id}>"


class PasswordResetToken(db.Model):
    """Password reset token model."""

    __tablename__ = "password_reset_tokens"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    token = db.Column(db.String(255), nullable=False, unique=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True), nullable=True)

    user = db.relationship("User", backref=db.backref("password_reset_tokens", lazy="dynamic"))

    def is_expired(self) -> bool:
        """Return True when token is expired."""

        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > expires_at

    def __repr__(self) -> str:
        """Represent password reset token for debugging."""

        return f"<PasswordResetToken {self.user_id}>"


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    """Load a user instance for Flask Login."""

    try:
        return db.session.get(User, UUIDType(user_id))
    except (ValueError, TypeError):
        return None
