"""Project model."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func

from app.extensions import db
from app.models.db_types import UUID


class Project(db.Model):
    """Project container for workflows and tasks."""

    __tablename__ = "projects"
    __table_args__ = (
        db.Index("idx_projects_org_id", "org_id"),
        db.Index("idx_projects_created_by", "created_by"),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id = db.Column(UUID(as_uuid=True), db.ForeignKey("organizations.id"), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_by = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    is_deleted = db.Column(db.Boolean, nullable=False, default=False)
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    color = db.Column(db.String(7), nullable=True)
    icon = db.Column(db.String(50), nullable=True)

    organization = db.relationship("Organization", backref=db.backref("projects", lazy="dynamic"))
    creator = db.relationship("User", backref=db.backref("projects_created", lazy="dynamic"))
    workflows = db.relationship("Workflow", back_populates="project", lazy="dynamic")
    tasks = db.relationship("AutomationTask", back_populates="project", lazy="dynamic")

    def __repr__(self) -> str:
        """Represent project for debugging."""

        return f"<Project {self.name}>"
