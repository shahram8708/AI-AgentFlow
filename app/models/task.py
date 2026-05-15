"""Task related models."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import BigInteger, func

from app.extensions import db
from app.models.db_types import JSONB, UUID


class AutomationTask(db.Model):
    """Main automation task execution model."""

    __tablename__ = "automation_tasks"
    __table_args__ = (
        db.Index("idx_tasks_org_status", "org_id", "status"),
        db.Index("idx_tasks_user_id", "user_id"),
        db.Index("idx_tasks_created_at", db.text("created_at DESC")),
        db.Index("idx_tasks_workflow_id", "workflow_id"),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id = db.Column(UUID(as_uuid=True), db.ForeignKey("organizations.id"), nullable=False)
    project_id = db.Column(UUID(as_uuid=True), db.ForeignKey("projects.id"), nullable=True)
    workflow_id = db.Column(UUID(as_uuid=True), db.ForeignKey("workflows.id"), nullable=True)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    task_type = db.Column(db.String(100), nullable=False)
    task_name = db.Column(db.String(255), nullable=True)
    input_json = db.Column(JSONB, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    priority = db.Column(db.String(10), nullable=False, default="default")
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    celery_task_id = db.Column(db.String(255), nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    retry_count = db.Column(db.Integer, nullable=False, default=0)
    timeout_seconds = db.Column(db.Integer, nullable=False, default=300)

    organization = db.relationship("Organization", backref=db.backref("tasks", lazy="dynamic"))
    project = db.relationship("Project", back_populates="tasks")
    workflow = db.relationship("Workflow", back_populates="tasks")
    user = db.relationship("User", backref=db.backref("automation_tasks", lazy="dynamic"))
    steps = db.relationship(
        "TaskStep", back_populates="task", cascade="all, delete-orphan", lazy="dynamic"
    )
    outputs = db.relationship(
        "TaskOutput", back_populates="task", cascade="all, delete-orphan", lazy="dynamic"
    )

    def __repr__(self) -> str:
        """Represent automation task for debugging."""

        return f"<AutomationTask {self.id} status={self.status}>"


class TaskStep(db.Model):
    """Task step execution details model."""

    __tablename__ = "task_steps"
    __table_args__ = (db.Index("idx_task_steps_task_id", "task_id"),)

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id = db.Column(
        UUID(as_uuid=True), db.ForeignKey("automation_tasks.id"), nullable=False
    )
    step_number = db.Column(db.Integer, nullable=False)
    step_name = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    input_json = db.Column(JSONB, nullable=True)
    output_json = db.Column(JSONB, nullable=True)
    error_msg = db.Column(db.Text, nullable=True)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    duration_ms = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)

    task = db.relationship("AutomationTask", back_populates="steps")

    def __repr__(self) -> str:
        """Represent task step for debugging."""

        return f"<TaskStep task={self.task_id} step={self.step_number}>"


class TaskOutput(db.Model):
    """Task output persistence model."""

    __tablename__ = "task_outputs"
    __table_args__ = (
        db.Index("idx_outputs_task_id", "task_id"),
        db.Index(
            "idx_outputs_org_created",
            "org_id",
            db.text("created_at DESC"),
            postgresql_where=db.text("is_deleted = false"),
        ),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id = db.Column(
        UUID(as_uuid=True), db.ForeignKey("automation_tasks.id"), nullable=False
    )
    org_id = db.Column(UUID(as_uuid=True), db.ForeignKey("organizations.id"), nullable=False)
    output_type = db.Column(db.String(20), nullable=False)
    content_text = db.Column(db.Text, nullable=True)
    file_path = db.Column(db.String(500), nullable=True)
    file_name = db.Column(db.String(255), nullable=True)
    file_size = db.Column(BigInteger, nullable=True)
    file_mime = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted = db.Column(db.Boolean, nullable=False, default=False)
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)

    task = db.relationship("AutomationTask", back_populates="outputs")
    organization = db.relationship("Organization", backref=db.backref("outputs", lazy="dynamic"))

    def __repr__(self) -> str:
        """Represent task output for debugging."""

        return f"<TaskOutput task={self.task_id} type={self.output_type}>"


db.Index(
    "idx_tasks_input_json",
    AutomationTask.input_json,
    postgresql_using="gin",
)

db.Index(
    "idx_active_tasks",
    AutomationTask.org_id,
    postgresql_where=AutomationTask.status.in_(["pending", "running"]),
)
