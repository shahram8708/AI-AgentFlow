"""Integration and credential related models."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func

from app.extensions import db
from app.models.db_types import JSONB, UUID


class Integration(db.Model):
    """Supported external integration catalog."""

    __tablename__ = "integrations"
    __table_args__ = (db.Index("idx_integrations_service_name", "service_name", unique=True),)

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    service_name = db.Column(db.String(100), nullable=False, unique=True)
    display_name = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    auth_type = db.Column(db.String(20), nullable=False)
    logo_url = db.Column(db.String(500), nullable=True)
    docs_url = db.Column(db.String(500), nullable=True)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    data_sources = db.relationship("DataSource", back_populates="integration", lazy="dynamic")

    def __repr__(self) -> str:
        """Represent integration for debugging."""

        return f"<Integration {self.service_name}>"


class DataSource(db.Model):
    """Organization data source configuration model."""

    __tablename__ = "data_sources"
    __table_args__ = (
        db.Index("idx_data_sources_org_id", "org_id"),
        db.Index("idx_data_sources_integration_id", "integration_id"),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id = db.Column(UUID(as_uuid=True), db.ForeignKey("organizations.id"), nullable=False)
    integration_id = db.Column(
        UUID(as_uuid=True), db.ForeignKey("integrations.id"), nullable=True
    )
    source_type = db.Column(db.String(100), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    config_json = db.Column(JSONB, nullable=False, default=dict)
    created_by = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    last_synced = db.Column(db.DateTime(timezone=True), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    is_deleted = db.Column(db.Boolean, nullable=False, default=False)
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)

    organization = db.relationship("Organization", backref=db.backref("data_sources", lazy="dynamic"))
    integration = db.relationship("Integration", back_populates="data_sources")
    creator = db.relationship("User", backref=db.backref("data_sources_created", lazy="dynamic"))

    def __repr__(self) -> str:
        """Represent data source for debugging."""

        return f"<DataSource {self.name}>"


class CredentialVault(db.Model):
    """Encrypted credential vault for integrations."""

    __tablename__ = "credentials_vault"
    __table_args__ = (db.Index("idx_vault_org_service", "org_id", "service_name"),)

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id = db.Column(UUID(as_uuid=True), db.ForeignKey("organizations.id"), nullable=False)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    service_name = db.Column(db.String(100), nullable=False)
    label = db.Column(db.String(255), nullable=False)
    encrypted_value = db.Column(db.Text, nullable=False)
    encryption_key_ref = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    last_used = db.Column(db.DateTime(timezone=True), nullable=True)

    organization = db.relationship(
        "Organization", backref=db.backref("credentials", lazy="dynamic")
    )
    user = db.relationship("User", backref=db.backref("credentials", lazy="dynamic"))

    def __repr__(self) -> str:
        """Represent vault credential for debugging."""

        return f"<CredentialVault {self.service_name}:{self.label}>"
