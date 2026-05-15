"""Organization and membership models."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import UniqueConstraint, func

from app.extensions import db
from app.models.db_types import JSONB, UUID


class Organization(db.Model):
    """Organization entity that groups users and resources."""

    __tablename__ = "organizations"
    __table_args__ = (db.Index("idx_organizations_slug", "slug", unique=True),)

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(100), nullable=False, unique=True)
    owner_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    plan_id = db.Column(UUID(as_uuid=True), db.ForeignKey("plans.id"), nullable=True)
    seats_used = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    settings_json = db.Column(JSONB, nullable=False, default=dict)
    is_deleted = db.Column(db.Boolean, nullable=False, default=False)
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)

    owner = db.relationship("User", back_populates="owned_organizations", foreign_keys=[owner_id])
    members = db.relationship(
        "OrganizationMember",
        back_populates="organization",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    plan = db.relationship("Plan", back_populates="organizations")
    subscription = db.relationship(
        "Subscription",
        back_populates="organization",
        uselist=False,
    )

    def __repr__(self) -> str:
        """Represent organization for debugging."""

        return f"<Organization {self.slug}>"


class OrganizationMember(db.Model):
    """Membership mapping between organization and users."""

    __tablename__ = "organization_members"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_org_members_org_user"),
        db.Index("idx_org_members_org_user", "org_id", "user_id"),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id = db.Column(
        UUID(as_uuid=True), db.ForeignKey("organizations.id"), nullable=False
    )
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    invited_at = db.Column(db.DateTime(timezone=True), nullable=True)
    joined_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    organization = db.relationship("Organization", back_populates="members")
    user = db.relationship("User", back_populates="organization_memberships")

    def __repr__(self) -> str:
        """Represent organization member for debugging."""

        return f"<OrganizationMember org={self.org_id} user={self.user_id}>"
