"""Billing and subscription related models."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func

from app.extensions import db
from app.models.db_types import JSONB, UUID


class Plan(db.Model):
    """Subscription plan model for organizations."""

    __tablename__ = "plans"
    __table_args__ = (db.Index("idx_plans_slug", "slug", unique=True),)

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(50), nullable=False, unique=True)
    price_monthly_inr = db.Column(db.Integer, nullable=False)
    price_annual_inr = db.Column(db.Integer, nullable=False)
    task_quota_monthly = db.Column(db.Integer, nullable=False)
    seat_limit = db.Column(db.Integer, nullable=False)
    output_retention_days = db.Column(db.Integer, nullable=False)
    features_json = db.Column(JSONB, nullable=False, default=dict)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    razorpay_plan_id_monthly = db.Column(db.String(255), nullable=True)
    razorpay_plan_id_annual = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    organizations = db.relationship("Organization", back_populates="plan", lazy="dynamic")
    subscriptions = db.relationship("Subscription", back_populates="plan", lazy="dynamic")

    @property
    def monthly_price_display(self) -> str:
        """Return formatted monthly pricing display string."""

        if self.price_monthly_inr < 0:
            return "Custom/month"
        return f"₹{self.price_monthly_inr // 100:,}/month"

    @property
    def annual_price_display(self) -> str:
        """Return formatted annual pricing display string."""

        if self.price_annual_inr < 0:
            return "Custom/year"
        return f"₹{self.price_annual_inr // 100:,}/year"

    @property
    def is_free(self) -> bool:
        """Return True if plan is free tier."""

        return self.price_monthly_inr == 0 and self.price_annual_inr == 0

    @property
    def is_unlimited(self) -> bool:
        """Return True if plan provides unlimited access."""

        return self.slug == "enterprise" or (
            self.task_quota_monthly == -1
            and self.seat_limit == -1
            and self.output_retention_days == -1
        )

    def __repr__(self) -> str:
        """Represent plan for debugging."""

        return f"<Plan {self.slug}>"


class Subscription(db.Model):
    """Organization subscription model backed by Razorpay."""

    __tablename__ = "subscriptions"
    __table_args__ = (db.Index("idx_subscriptions_org_id", "org_id"),)

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id = db.Column(UUID(as_uuid=True), db.ForeignKey("organizations.id"), nullable=False)
    plan_id = db.Column(UUID(as_uuid=True), db.ForeignKey("plans.id"), nullable=False)
    razorpay_subscription_id = db.Column(db.String(255), nullable=True)
    razorpay_customer_id = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(30), nullable=False)
    billing_cycle = db.Column(db.String(10), nullable=False, default="monthly")
    current_period_start = db.Column(db.DateTime(timezone=True), nullable=True)
    current_period_end = db.Column(db.DateTime(timezone=True), nullable=True)
    cancel_at = db.Column(db.DateTime(timezone=True), nullable=True)
    cancelled_at = db.Column(db.DateTime(timezone=True), nullable=True)
    trial_end = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    organization = db.relationship("Organization", back_populates="subscription")
    plan = db.relationship("Plan", back_populates="subscriptions")
    invoices = db.relationship("Invoice", back_populates="subscription", lazy="dynamic")

    def __repr__(self) -> str:
        """Represent subscription for debugging."""

        return f"<Subscription org={self.org_id} status={self.status}>"


class Invoice(db.Model):
    """Invoice record for billing events."""

    __tablename__ = "invoices"
    __table_args__ = (db.Index("idx_invoices_org_created", "org_id", db.text("created_at DESC")),)

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id = db.Column(UUID(as_uuid=True), db.ForeignKey("organizations.id"), nullable=False)
    subscription_id = db.Column(
        UUID(as_uuid=True), db.ForeignKey("subscriptions.id"), nullable=True
    )
    razorpay_payment_id = db.Column(db.String(255), nullable=True)
    razorpay_order_id = db.Column(db.String(255), nullable=True)
    amount_paise = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="INR")
    status = db.Column(db.String(20), nullable=False)
    description = db.Column(db.String(500), nullable=True)
    due_date = db.Column(db.DateTime(timezone=True), nullable=True)
    paid_at = db.Column(db.DateTime(timezone=True), nullable=True)
    pdf_url = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    organization = db.relationship("Organization", backref=db.backref("invoices", lazy="dynamic"))
    subscription = db.relationship("Subscription", back_populates="invoices")

    @property
    def amount_display(self) -> str:
        """Return invoice amount in INR display format."""

        rupees = self.amount_paise // 100
        return f"₹{rupees:,}"

    def __repr__(self) -> str:
        """Represent invoice for debugging."""

        return f"<Invoice {self.id} amount={self.amount_paise}>"
