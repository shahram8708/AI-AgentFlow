"""Audit log model."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func

from app.extensions import db
from app.models.db_types import JSONB, UUID


class AuditLog(db.Model):
    """Append only audit trail model."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        db.Index("idx_audit_org_timestamp", "org_id", db.text("timestamp DESC")),
        db.Index("idx_audit_user_id", "user_id"),
        db.Index("idx_audit_action", "action"),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id = db.Column(UUID(as_uuid=True), db.ForeignKey("organizations.id"), nullable=True)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=True)
    action = db.Column(db.String(100), nullable=False)
    resource_type = db.Column(db.String(50), nullable=True)
    resource_id = db.Column(db.String(255), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(500), nullable=True)
    timestamp = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    before_json = db.Column(JSONB, nullable=True)
    after_json = db.Column(JSONB, nullable=True)
    extra_json = db.Column(JSONB, nullable=True)

    organization = db.relationship("Organization", backref=db.backref("audit_logs", lazy="dynamic"))
    user = db.relationship("User", backref=db.backref("audit_logs", lazy="dynamic"))

    def __repr__(self) -> str:
        """Represent audit log for debugging."""

        return f"<AuditLog {self.action}>"
