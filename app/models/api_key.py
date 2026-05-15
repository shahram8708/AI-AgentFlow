"""API key model."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func

from app.extensions import db
from app.models.db_types import JSONB, UUID


class ApiKey(db.Model):
    """Stored API keys for external API access."""

    __tablename__ = "api_keys"
    __table_args__ = (
        db.Index("idx_api_keys_org_id", "org_id"),
        db.Index("idx_api_keys_user_id", "user_id"),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id = db.Column(UUID(as_uuid=True), db.ForeignKey("organizations.id"), nullable=False)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    key_hash = db.Column(db.String(255), nullable=False)
    key_prefix = db.Column(db.String(8), nullable=False)
    label = db.Column(db.String(255), nullable=False)
    last_used = db.Column(db.DateTime(timezone=True), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    scopes_json = db.Column(JSONB, nullable=False, default=lambda: ["read", "write"])
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    organization = db.relationship("Organization", backref=db.backref("api_keys", lazy="dynamic"))
    user = db.relationship("User", backref=db.backref("api_keys", lazy="dynamic"))

    def __repr__(self) -> str:
        """Represent API key for debugging."""

        return f"<ApiKey {self.key_prefix}>"
