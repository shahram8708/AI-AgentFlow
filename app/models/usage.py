"""Usage, feature flag, and support ticket models."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func

from app.extensions import db
from app.models.db_types import JSONB, UUID


class UsageRecord(db.Model):
    """Usage accounting model."""

    __tablename__ = "usage_records"
    __table_args__ = (
        db.Index("idx_usage_org_type_date", "org_id", "usage_type", db.text("recorded_at DESC")),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id = db.Column(UUID(as_uuid=True), db.ForeignKey("organizations.id"), nullable=False)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    task_id = db.Column(UUID(as_uuid=True), db.ForeignKey("automation_tasks.id"), nullable=True)
    usage_type = db.Column(db.String(30), nullable=False)
    units_consumed = db.Column(db.Integer, nullable=False, default=1)
    recorded_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)

    organization = db.relationship("Organization", backref=db.backref("usage_records", lazy="dynamic"))
    user = db.relationship("User", backref=db.backref("usage_records", lazy="dynamic"))
    task = db.relationship("AutomationTask", backref=db.backref("usage_records", lazy="dynamic"))

    def __repr__(self) -> str:
        """Represent usage record for debugging."""

        return f"<UsageRecord {self.usage_type}:{self.units_consumed}>"


class FeatureFlag(db.Model):
    """Feature flag rollout model."""

    __tablename__ = "feature_flags"
    __table_args__ = (db.Index("idx_feature_flags_key", "flag_key", unique=True),)

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    flag_key = db.Column(db.String(100), nullable=False, unique=True)
    display_name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_enabled = db.Column(db.Boolean, nullable=False, default=False)
    rollout_percentage = db.Column(db.Integer, nullable=False, default=0)
    enabled_org_ids = db.Column(JSONB, nullable=False, default=list)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        """Represent feature flag for debugging."""

        return f"<FeatureFlag {self.flag_key}>"


class SupportTicket(db.Model):
    """Support ticket model."""

    __tablename__ = "support_tickets"
    __table_args__ = (db.Index("idx_support_tickets_org_status", "org_id", "status"),)

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    org_id = db.Column(UUID(as_uuid=True), db.ForeignKey("organizations.id"), nullable=False)
    subject = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="open")
    priority = db.Column(db.String(10), nullable=False, default="medium")
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    resolved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    is_deleted = db.Column(db.Boolean, nullable=False, default=False)
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)

    user = db.relationship("User", backref=db.backref("support_tickets", lazy="dynamic"))
    organization = db.relationship(
        "Organization", backref=db.backref("support_tickets", lazy="dynamic")
    )

    def __repr__(self) -> str:
        """Represent support ticket for debugging."""

        return f"<SupportTicket {self.subject}>"
