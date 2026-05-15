"""Notification model."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func

from app.extensions import db
from app.models.db_types import UUID


class Notification(db.Model):
    """User notification model."""

    __tablename__ = "notifications"
    __table_args__ = (
        db.Index(
            "idx_notifications_user_unread",
            "user_id",
            "is_read",
            postgresql_where=db.text("is_deleted = false"),
        ),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    org_id = db.Column(UUID(as_uuid=True), db.ForeignKey("organizations.id"), nullable=False)
    type = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    action_url = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted = db.Column(db.Boolean, nullable=False, default=False)
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)

    user = db.relationship("User", backref=db.backref("notifications", lazy="dynamic"))
    organization = db.relationship(
        "Organization", backref=db.backref("notifications", lazy="dynamic")
    )

    def __repr__(self) -> str:
        """Represent notification for debugging."""

        return f"<Notification {self.type}>"
