"""Workflow and workflow template models."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func

from app.extensions import db
from app.models.db_types import JSONB, UUID


class Workflow(db.Model):
    """Automation workflow model."""

    __tablename__ = "workflows"
    __table_args__ = (
        db.Index("idx_workflows_org_id", "org_id"),
        db.Index("idx_workflows_project_id", "project_id"),
        db.Index("idx_workflows_created_at", db.text("created_at DESC")),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = db.Column(UUID(as_uuid=True), db.ForeignKey("projects.id"), nullable=True)
    org_id = db.Column(UUID(as_uuid=True), db.ForeignKey("organizations.id"), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    steps_json = db.Column(JSONB, nullable=False, default=list)
    trigger_type = db.Column(db.String(20), nullable=False, default="manual")
    is_public = db.Column(db.Boolean, nullable=False, default=False)
    created_by = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    is_deleted = db.Column(db.Boolean, nullable=False, default=False)
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_run_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_run_status = db.Column(db.String(20), nullable=True)
    run_count = db.Column(db.Integer, nullable=False, default=0)
    tags_json = db.Column(JSONB, nullable=False, default=list)

    project = db.relationship("Project", back_populates="workflows")
    organization = db.relationship("Organization", backref=db.backref("workflows", lazy="dynamic"))
    creator = db.relationship("User", backref=db.backref("workflows_created", lazy="dynamic"))
    tasks = db.relationship("AutomationTask", back_populates="workflow", lazy="dynamic")
    scheduled_jobs = db.relationship(
        "ScheduledJob", back_populates="workflow", cascade="all, delete-orphan", lazy="dynamic"
    )

    def __repr__(self) -> str:
        """Represent workflow for debugging."""

        return f"<Workflow {self.name}>"


class WorkflowTemplate(db.Model):
    """Reusable workflow templates for launchers."""

    __tablename__ = "workflow_templates"
    __table_args__ = (
        db.Index("idx_workflow_templates_category", "category"),
        db.Index("idx_workflow_templates_featured", "is_featured"),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    subcategory = db.Column(db.String(100), nullable=True)
    description = db.Column(db.Text, nullable=True)
    steps_json = db.Column(JSONB, nullable=False, default=list)
    required_integrations = db.Column(JSONB, nullable=False, default=list)
    usage_count = db.Column(db.Integer, nullable=False, default=0)
    is_featured = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    icon = db.Column(db.String(100), nullable=True)
    preview_output = db.Column(db.Text, nullable=True)
    estimated_time_seconds = db.Column(db.Integer, nullable=True)
    difficulty = db.Column(db.String(20), nullable=False, default="beginner")
    tags_json = db.Column(JSONB, nullable=False, default=list)

    def __repr__(self) -> str:
        """Represent workflow template for debugging."""

        return f"<WorkflowTemplate {self.name}>"
