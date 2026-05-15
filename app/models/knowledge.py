"""Knowledge base models."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import BigInteger, func

from app.extensions import db
from app.models.db_types import UUID


class KnowledgeBaseEntry(db.Model):
    """Organization knowledge base entry model."""

    __tablename__ = "knowledge_base_entries"
    __table_args__ = (
        db.Index("idx_kb_org_id", "org_id"),
        db.Index("idx_kb_project_id", "project_id"),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id = db.Column(UUID(as_uuid=True), db.ForeignKey("organizations.id"), nullable=False)
    project_id = db.Column(UUID(as_uuid=True), db.ForeignKey("projects.id"), nullable=True)
    title = db.Column(db.String(500), nullable=False)
    content_text = db.Column(db.Text, nullable=True)
    file_path = db.Column(db.String(500), nullable=True)
    file_name = db.Column(db.String(255), nullable=True)
    file_mime = db.Column(db.String(100), nullable=True)
    file_size = db.Column(BigInteger, nullable=True)
    source_url = db.Column(db.String(500), nullable=True)
    source_type = db.Column(db.String(30), nullable=False, default="text")
    created_by = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    is_deleted = db.Column(db.Boolean, nullable=False, default=False)
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)

    organization = db.relationship(
        "Organization", backref=db.backref("knowledge_entries", lazy="dynamic")
    )
    project = db.relationship("Project", backref=db.backref("knowledge_entries", lazy="dynamic"))
    creator = db.relationship("User", backref=db.backref("knowledge_entries", lazy="dynamic"))

    def __repr__(self) -> str:
        """Represent knowledge base entry for debugging."""

        return f"<KnowledgeBaseEntry {self.title}>"
