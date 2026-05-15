"""Scheduling models."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func

from app.extensions import db
from app.models.db_types import UUID


class ScheduledJob(db.Model):
    """Scheduled workflow execution model."""

    __tablename__ = "scheduled_jobs"
    __table_args__ = (db.Index("idx_scheduled_jobs_org_id", "org_id"),)

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id = db.Column(UUID(as_uuid=True), db.ForeignKey("organizations.id"), nullable=False)
    workflow_id = db.Column(UUID(as_uuid=True), db.ForeignKey("workflows.id"), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    cron_expression = db.Column(db.String(100), nullable=False)
    timezone = db.Column(db.String(100), nullable=False, default="Asia/Kolkata")
    next_run_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_run_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_run_status = db.Column(db.String(20), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_by = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    organization = db.relationship(
        "Organization", backref=db.backref("scheduled_jobs", lazy="dynamic")
    )
    workflow = db.relationship("Workflow", back_populates="scheduled_jobs")
    creator = db.relationship("User", backref=db.backref("scheduled_jobs", lazy="dynamic"))

    def __repr__(self) -> str:
        """Represent scheduled job for debugging."""

        return f"<ScheduledJob {self.name}>"
