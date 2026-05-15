"""Billing service backed by Razorpay."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from typing import Any
from uuid import UUID

import pytz
import razorpay
from flask import current_app
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from sqlalchemy import func

from app.extensions import cache, db
from app.models import AuditLog, Invoice, Organization, Plan, Subscription, User
from app.services.notification_service import NotificationService


class BillingServiceError(Exception):
    """Raised for safe, user facing billing workflow failures."""


class PaymentVerificationError(Exception):
    """Raised when payment or webhook signatures are invalid."""


class BillingService:
    """Service encapsulating Razorpay operations and billing state."""

    def __init__(self) -> None:
        self._client: razorpay.Client | None = None
        self.notification_service = NotificationService()

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _to_uuid(value: str) -> UUID:
        try:
            return UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise BillingServiceError("Invalid billing identifier") from exc

    @staticmethod
    def _format_inr(amount: float) -> str:
        return f"₹{amount:,.2f}"

    @staticmethod
    def _to_ist(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(pytz.timezone("Asia/Kolkata"))

    def _format_ist_datetime(self, value: datetime | None) -> str:
        local_value = self._to_ist(value)
        if local_value is None:
            return ""
        return local_value.strftime("%d %b %Y, %I:%M %p IST")

    def _get_client(self) -> razorpay.Client:
        """Initialize and return Razorpay client lazily."""

        if self._client is not None:
            return self._client

        key_id = os.environ.get("RAZORPAY_KEY_ID")
        key_secret = os.environ.get("RAZORPAY_KEY_SECRET")
        if not key_id or not key_secret:
            raise BillingServiceError("Razorpay credentials not configured")

        self._client = razorpay.Client(auth=(key_id, key_secret))
        return self._client

    @staticmethod
    def _billing_cache_key(org_id: str) -> str:
        return f"billing_summary:{org_id}"

    def _clear_billing_cache(self, org_id: str) -> None:
        try:
            cache.delete(self._billing_cache_key(org_id))
        except Exception as exc:  # pylint: disable=broad-except
            current_app.logger.warning("Failed to clear billing cache for org %s: %s", org_id, exc)

    def create_order(
        self,
        amount_inr: float,
        org_id: str,
        plan_id: str,
        billing_cycle: str,
    ) -> dict[str, Any]:
        """Create Razorpay order in paise from INR amount."""

        amount_paise = int(Decimal(str(amount_inr)) * 100)
        payload = {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": f"order_{str(org_id)[:8]}_{int(time.time())}",
            "notes": {
                "org_id": str(org_id),
                "plan_id": str(plan_id),
                "billing_cycle": billing_cycle,
                "platform": "agentflow",
            },
        }

        try:
            return self._get_client().order.create(payload)
        except Exception as exc:  # pylint: disable=broad-except
            current_app.logger.error(
                "Failed to create Razorpay order for org %s and plan %s: %s",
                org_id,
                plan_id,
                exc,
            )
            raise BillingServiceError(f"Failed to create payment order: {str(exc)}") from exc

    def verify_payment_signature(
        self,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: str,
    ) -> bool:
        """Verify payment signature using Razorpay utility helper."""

        try:
            self._get_client().utility.verify_payment_signature(
                {
                    "razorpay_order_id": razorpay_order_id,
                    "razorpay_payment_id": razorpay_payment_id,
                    "razorpay_signature": razorpay_signature,
                }
            )
            return True
        except Exception as exc:  # pylint: disable=broad-except
            current_app.logger.warning(
                "Payment signature verification failed for order %s: %s",
                razorpay_order_id,
                exc,
            )
            return False

    def verify_webhook_signature(self, webhook_body: bytes, webhook_signature: str) -> bool:
        """Validate webhook HMAC-SHA256 signature."""

        webhook_secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
        if not webhook_secret:
            current_app.logger.warning("RAZORPAY_WEBHOOK_SECRET is not configured")
            return False

        digest = hmac.new(webhook_secret.encode("utf-8"), webhook_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(digest, webhook_signature or "")

    def activate_subscription(
        self,
        org_id: str,
        plan_id: str,
        razorpay_payment_id: str,
        razorpay_order_id: str,
        billing_cycle: str,
    ) -> Subscription:
        """Activate subscription, issue invoice, and update plan in one transaction."""

        org_uuid = self._to_uuid(org_id)
        plan_uuid = self._to_uuid(plan_id)
        now = self._utcnow()

        try:
            organization = db.session.get(Organization, org_uuid)
            if organization is None:
                raise BillingServiceError("Organization not found")

            existing_invoice = Invoice.query.filter_by(razorpay_payment_id=razorpay_payment_id).first()
            if existing_invoice is not None:
                if existing_invoice.org_id != organization.id:
                    raise BillingServiceError("Payment reference already exists for another organization")

                if existing_invoice.subscription is not None:
                    return existing_invoice.subscription

                existing_subscription = (
                    Subscription.query.filter_by(org_id=organization.id, status="active")
                    .order_by(Subscription.created_at.desc())
                    .first()
                )
                if existing_subscription is not None:
                    return existing_subscription

            plan = db.session.get(Plan, plan_uuid)
            if plan is None or not plan.is_active:
                raise BillingServiceError("Selected billing plan is not available")

            Subscription.query.filter_by(org_id=organization.id, status="active").update(
                {
                    "status": "cancelled",
                    "cancelled_at": now,
                },
                synchronize_session=False,
            )

            cycle = "annual" if billing_cycle == "annual" else "monthly"
            period_end = now + timedelta(days=365 if cycle == "annual" else 30)
            amount_paise = plan.price_annual_inr if cycle == "annual" else plan.price_monthly_inr

            subscription = Subscription(
                org_id=organization.id,
                plan_id=plan.id,
                razorpay_subscription_id=razorpay_payment_id,
                status="active",
                billing_cycle=cycle,
                current_period_start=now,
                current_period_end=period_end,
            )
            db.session.add(subscription)
            db.session.flush()

            organization.plan_id = plan.id

            invoice = Invoice(
                org_id=organization.id,
                subscription_id=subscription.id,
                razorpay_payment_id=razorpay_payment_id,
                razorpay_order_id=razorpay_order_id,
                amount_paise=amount_paise,
                currency="INR",
                status="paid",
                paid_at=now,
                due_date=period_end,
                description=f"{plan.name} ({cycle}) subscription",
            )
            db.session.add(invoice)

            db.session.add(
                AuditLog(
                    org_id=organization.id,
                    user_id=organization.owner_id,
                    action="billing.plan_upgraded",
                    resource_type="subscription",
                    resource_id=str(subscription.id),
                    extra_json={
                        "plan_name": plan.name,
                        "billing_cycle": cycle,
                        "amount_paise": amount_paise,
                        "payment_id": razorpay_payment_id,
                    },
                )
            )

            db.session.commit()
            self._clear_billing_cache(str(organization.id))
            return subscription
        except BillingServiceError:
            db.session.rollback()
            raise
        except Exception as exc:  # pylint: disable=broad-except
            db.session.rollback()
            current_app.logger.error("Failed to activate subscription for org %s: %s", org_id, exc)
            raise BillingServiceError("Unable to activate subscription right now") from exc

    def cancel_subscription(self, org_id: str, reason: str = "") -> bool:
        """Cancel active subscription while preserving access until period end."""

        org_uuid = self._to_uuid(org_id)
        now = self._utcnow()

        try:
            subscription = Subscription.query.filter_by(org_id=org_uuid, status="active").first()
            if subscription is None:
                raise BillingServiceError("No active subscription found")

            subscription.status = "cancelled"
            subscription.cancelled_at = now
            subscription.cancel_at = subscription.current_period_end

            db.session.add(
                AuditLog(
                    org_id=subscription.org_id,
                    action="billing.subscription_cancelled",
                    resource_type="subscription",
                    resource_id=str(subscription.id),
                    extra_json={
                        "reason": reason,
                        "cancel_at": (
                            subscription.cancel_at.isoformat() if subscription.cancel_at else None
                        ),
                    },
                )
            )

            db.session.commit()
            self._clear_billing_cache(str(org_uuid))
            return True
        except BillingServiceError:
            db.session.rollback()
            raise
        except Exception as exc:  # pylint: disable=broad-except
            db.session.rollback()
            current_app.logger.error("Failed to cancel subscription for org %s: %s", org_id, exc)
            raise BillingServiceError("Unable to cancel subscription right now") from exc

    def get_invoice_pdf(self, invoice_id: str, org_id: str) -> BytesIO:
        """Generate professional A4 GST invoice PDF for organization invoice."""

        invoice_uuid = self._to_uuid(invoice_id)
        org_uuid = self._to_uuid(org_id)

        invoice = Invoice.query.filter_by(id=invoice_uuid, org_id=org_uuid).first()
        if invoice is None:
            raise BillingServiceError("Invoice not found")

        organization = db.session.get(Organization, org_uuid)
        if organization is None:
            raise BillingServiceError("Organization not found")

        owner = db.session.get(User, organization.owner_id) if organization.owner_id else None
        subscription = invoice.subscription
        plan = subscription.plan if subscription and subscription.plan else None
        plan_name = plan.name if plan else "Current"
        billing_cycle = subscription.billing_cycle if subscription else "monthly"

        period_start = (
            subscription.current_period_start if subscription and subscription.current_period_start else invoice.created_at
        )
        period_end = (
            subscription.current_period_end if subscription and subscription.current_period_end else invoice.due_date
        )

        subtotal = Decimal(invoice.amount_paise or 0) / Decimal("100")
        gst_amount = (subtotal * Decimal("0.18")).quantize(Decimal("0.01"))
        total_with_gst = (subtotal + gst_amount).quantize(Decimal("0.01"))

        invoice_number = f"INV-{self._to_ist(invoice.created_at).year if self._to_ist(invoice.created_at) else datetime.now().year}-{str(invoice.id)[:8].upper()}"
        invoice_date = self._format_ist_datetime(invoice.created_at)
        due_date = self._format_ist_datetime(invoice.due_date or invoice.created_at)
        payment_date = self._format_ist_datetime(invoice.paid_at)
        status_text = "PAID" if (invoice.status or "").lower() == "paid" else "UNPAID"
        status_color = colors.HexColor("#16A34A") if status_text == "PAID" else colors.HexColor("#DC2626")

        billing_address = ""
        if isinstance(organization.settings_json, dict):
            billing_address = str(organization.settings_json.get("billing_address") or "")

        bill_to_name = owner.get_full_name() if owner else "Organization Owner"
        service_description = (
            f"AgentFlow {plan_name} Plan - {billing_cycle.title()} Subscription"
        )
        period_text = f"{self._format_ist_datetime(period_start)} to {self._format_ist_datetime(period_end)}"

        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4

        margin_x = 0.7 * inch
        y = height - 0.8 * inch

        pdf.setFont("Helvetica-Bold", 20)
        pdf.setFillColor(colors.HexColor("#0F172A"))
        pdf.drawString(margin_x, y, "TAX INVOICE")

        y -= 0.35 * inch
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(margin_x, y, "AgentFlow Technologies Pvt. Ltd.")

        y -= 0.2 * inch
        pdf.setFont("Helvetica", 10)
        pdf.drawString(margin_x, y, "123 SG Highway, Ahmedabad, Gujarat 380054, India")
        y -= 0.18 * inch
        pdf.drawString(margin_x, y, "GSTIN: 24XXXXXXXXXXXXX")
        y -= 0.18 * inch
        pdf.drawString(margin_x, y, "Email: billing@agentflow.ai | Phone: +91 79 4000 1000")

        box_w = 2.9 * inch
        box_h = 1.5 * inch
        box_x = width - margin_x - box_w
        box_y = height - 0.8 * inch - box_h
        pdf.setStrokeColor(colors.HexColor("#CBD5E1"))
        pdf.setFillColor(colors.white)
        pdf.roundRect(box_x, box_y, box_w, box_h, 6, stroke=1, fill=1)

        text_x = box_x + 0.16 * inch
        text_y = box_y + box_h - 0.24 * inch
        pdf.setFont("Helvetica-Bold", 9)
        pdf.setFillColor(colors.HexColor("#334155"))
        pdf.drawString(text_x, text_y, "Invoice Number")
        pdf.setFont("Helvetica", 9)
        pdf.drawRightString(box_x + box_w - 0.16 * inch, text_y, invoice_number)

        text_y -= 0.2 * inch
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(text_x, text_y, "Invoice Date")
        pdf.setFont("Helvetica", 9)
        pdf.drawRightString(box_x + box_w - 0.16 * inch, text_y, invoice_date)

        text_y -= 0.2 * inch
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(text_x, text_y, "Due Date")
        pdf.setFont("Helvetica", 9)
        pdf.drawRightString(box_x + box_w - 0.16 * inch, text_y, due_date)

        text_y -= 0.28 * inch
        badge_w = 0.9 * inch
        badge_h = 0.22 * inch
        badge_x = box_x + box_w - badge_w - 0.16 * inch
        pdf.setFillColor(status_color)
        pdf.roundRect(badge_x, text_y - 0.02 * inch, badge_w, badge_h, 4, stroke=0, fill=1)
        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawCentredString(badge_x + (badge_w / 2), text_y + 0.05 * inch, status_text)

        y -= 0.7 * inch
        pdf.setFillColor(colors.HexColor("#0F172A"))
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(margin_x, y, "Bill To")
        y -= 0.2 * inch
        pdf.setFont("Helvetica", 10)
        pdf.drawString(margin_x, y, organization.name)
        y -= 0.17 * inch
        pdf.drawString(margin_x, y, bill_to_name)
        if billing_address:
            y -= 0.17 * inch
            pdf.drawString(margin_x, y, billing_address[:95])

        y -= 0.35 * inch
        table_x = margin_x
        table_w = width - (2 * margin_x)
        row_h = 0.28 * inch

        pdf.setFillColor(colors.HexColor("#E2E8F0"))
        pdf.rect(table_x, y - row_h, table_w, row_h, stroke=0, fill=1)
        pdf.setFillColor(colors.HexColor("#0F172A"))
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(table_x + 0.12 * inch, y - 0.18 * inch, "Description")
        pdf.drawString(table_x + 3.65 * inch, y - 0.18 * inch, "Period")
        pdf.drawRightString(table_x + table_w - 0.12 * inch, y - 0.18 * inch, "Amount")

        y -= row_h
        pdf.setFillColor(colors.white)
        pdf.rect(table_x, y - row_h, table_w, row_h, stroke=1, fill=1)
        pdf.setFont("Helvetica", 9)
        pdf.setFillColor(colors.HexColor("#1E293B"))
        pdf.drawString(table_x + 0.12 * inch, y - 0.18 * inch, service_description[:60])
        pdf.drawString(table_x + 3.65 * inch, y - 0.18 * inch, period_text[:28])
        pdf.drawRightString(
            table_x + table_w - 0.12 * inch,
            y - 0.18 * inch,
            self._format_inr(float(subtotal)),
        )

        y -= 0.55 * inch
        summary_x = width - margin_x - 2.4 * inch
        line_gap = 0.22 * inch
        pdf.setFont("Helvetica", 10)
        pdf.setFillColor(colors.HexColor("#334155"))
        pdf.drawString(summary_x, y, "Subtotal")
        pdf.drawRightString(width - margin_x, y, self._format_inr(float(subtotal)))
        y -= line_gap
        pdf.drawString(summary_x, y, "GST (18%)")
        pdf.drawRightString(width - margin_x, y, self._format_inr(float(gst_amount)))
        y -= 0.06 * inch
        pdf.setStrokeColor(colors.HexColor("#CBD5E1"))
        pdf.line(summary_x, y, width - margin_x, y)
        y -= line_gap
        pdf.setFillColor(colors.HexColor("#0F172A"))
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(summary_x, y, "Total Amount")
        pdf.drawRightString(width - margin_x, y, self._format_inr(float(total_with_gst)))

        y -= 0.42 * inch
        pdf.setFillColor(colors.HexColor("#0F172A"))
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(margin_x, y, "Payment Details")

        y -= 0.2 * inch
        pdf.setFont("Helvetica", 9)
        pdf.setFillColor(colors.HexColor("#334155"))
        pdf.drawString(margin_x, y, "Payment Method: Razorpay")
        y -= 0.17 * inch
        pdf.drawString(margin_x, y, f"Payment ID: {invoice.razorpay_payment_id or 'N/A'}")
        y -= 0.17 * inch
        pdf.drawString(margin_x, y, f"Order ID: {invoice.razorpay_order_id or 'N/A'}")
        y -= 0.17 * inch
        pdf.drawString(margin_x, y, f"Payment Date: {payment_date or 'N/A'}")

        footer_y = 0.8 * inch
        pdf.setFont("Helvetica", 9)
        pdf.setFillColor(colors.HexColor("#475569"))
        pdf.drawString(
            margin_x,
            footer_y,
            "Thank you for your business! For billing queries: billing@agentflow.ai",
        )
        pdf.drawString(margin_x, footer_y - 0.16 * inch, "This is a computer-generated invoice.")

        pdf.showPage()
        pdf.save()
        buffer.seek(0)
        return buffer

    def _extract_entity(self, event_data: dict[str, Any], key: str) -> dict[str, Any]:
        value = event_data.get(key)
        if isinstance(value, dict) and isinstance(value.get("entity"), dict):
            return value.get("entity") or {}
        if isinstance(value, dict):
            return value
        return {}

    def handle_webhook_event(self, event_type: str, event_data: dict[str, Any]) -> bool:
        """Handle webhook event payloads from Razorpay."""

        event_name = (event_type or "").strip()

        try:
            if event_name == "payment.captured":
                payment = self._extract_entity(event_data, "payment")
                notes = payment.get("notes") or {}
                org_id = str(notes.get("org_id") or "").strip()
                plan_id = str(notes.get("plan_id") or "").strip()
                billing_cycle = str(notes.get("billing_cycle") or "monthly").lower()
                payment_id = str(payment.get("id") or "").strip()
                order_id = str(payment.get("order_id") or "").strip()

                if not all([org_id, plan_id, payment_id, order_id]):
                    current_app.logger.warning("payment.captured missing required notes or IDs")
                    return True

                existing_invoice = Invoice.query.filter_by(razorpay_payment_id=payment_id).first()
                if existing_invoice is not None:
                    return True

                subscription = self.activate_subscription(
                    org_id=org_id,
                    plan_id=plan_id,
                    razorpay_payment_id=payment_id,
                    razorpay_order_id=order_id,
                    billing_cycle=billing_cycle,
                )

                organization = db.session.get(Organization, subscription.org_id)
                plan = db.session.get(Plan, subscription.plan_id)
                if organization and plan:
                    self.notification_service.notify_billing_event(
                        organization.id,
                        organization.owner_id,
                        "upgraded",
                        plan.name,
                    )
                return True

            if event_name == "payment.failed":
                payment = self._extract_entity(event_data, "payment")
                notes = payment.get("notes") or {}
                org_id = str(notes.get("org_id") or "").strip()
                current_app.logger.warning(
                    "Payment failed for payment_id=%s org_id=%s",
                    payment.get("id"),
                    org_id,
                )

                if org_id:
                    org_uuid = self._to_uuid(org_id)
                    Subscription.query.filter_by(org_id=org_uuid, status="pending").update(
                        {"status": "past_due"},
                        synchronize_session=False,
                    )
                    organization = db.session.get(Organization, org_uuid)
                    if organization:
                        plan = db.session.get(Plan, organization.plan_id) if organization.plan_id else None
                        self.notification_service.notify_billing_event(
                            organization.id,
                            organization.owner_id,
                            "payment_failed",
                            plan.name if plan else "Current",
                        )
                    db.session.commit()
                return True

            if event_name == "subscription.activated":
                subscription_entity = self._extract_entity(event_data, "subscription")
                razorpay_subscription_id = str(subscription_entity.get("id") or "").strip()
                if razorpay_subscription_id:
                    Subscription.query.filter_by(
                        razorpay_subscription_id=razorpay_subscription_id
                    ).update({"status": "active"}, synchronize_session=False)
                    db.session.commit()
                return True

            if event_name == "subscription.cancelled":
                subscription_entity = self._extract_entity(event_data, "subscription")
                razorpay_subscription_id = str(subscription_entity.get("id") or "").strip()
                if not razorpay_subscription_id:
                    return True

                subscription = Subscription.query.filter_by(
                    razorpay_subscription_id=razorpay_subscription_id
                ).first()
                if subscription is None:
                    return True

                subscription.status = "cancelled"
                subscription.cancelled_at = self._utcnow()

                organization = db.session.get(Organization, subscription.org_id)
                free_plan = Plan.query.filter_by(slug="free", is_active=True).first()
                if organization and free_plan:
                    organization.plan_id = free_plan.id

                plan_name = subscription.plan.name if subscription.plan else "Current"
                if organization:
                    self.notification_service.notify_billing_event(
                        organization.id,
                        organization.owner_id,
                        "cancelled",
                        plan_name,
                    )

                db.session.commit()
                self._clear_billing_cache(str(subscription.org_id))
                return True

            if event_name == "invoice.paid":
                invoice_entity = self._extract_entity(event_data, "invoice")
                order_id = str(invoice_entity.get("order_id") or "").strip()
                payment_id = str(invoice_entity.get("payment_id") or "").strip()

                invoice = None
                if order_id:
                    invoice = Invoice.query.filter_by(razorpay_order_id=order_id).first()
                if invoice is None and payment_id:
                    invoice = Invoice.query.filter_by(razorpay_payment_id=payment_id).first()

                if invoice is not None:
                    invoice.status = "paid"
                    invoice.paid_at = self._utcnow()
                    db.session.commit()
                    self._clear_billing_cache(str(invoice.org_id))
                return True

            current_app.logger.warning("Unhandled Razorpay webhook event: %s", event_name)
            return True
        except BillingServiceError as exc:
            current_app.logger.error("Billing service error while handling webhook %s: %s", event_name, exc)
            return True
        except Exception as exc:  # pylint: disable=broad-except
            db.session.rollback()
            current_app.logger.error("Unexpected webhook handling error for %s: %s", event_name, exc)
            return True

    def get_billing_summary(self, org_id: str) -> dict[str, Any]:
        """Return summary payload for billing settings view."""

        org_uuid = self._to_uuid(org_id)
        cache_key = self._billing_cache_key(org_id)

        cached_summary = cache.get(cache_key)
        if isinstance(cached_summary, dict):
            current_plan = (
                db.session.get(Plan, self._to_uuid(cached_summary["current_plan_id"]))
                if cached_summary.get("current_plan_id")
                else None
            )
            current_subscription = (
                db.session.get(Subscription, self._to_uuid(cached_summary["current_subscription_id"]))
                if cached_summary.get("current_subscription_id")
                else None
            )

            invoice_ids = cached_summary.get("invoice_ids") or []
            invoices = []
            for invoice_id in invoice_ids:
                try:
                    invoice = db.session.get(Invoice, self._to_uuid(invoice_id))
                except BillingServiceError:
                    invoice = None
                if invoice is not None:
                    invoices.append(invoice)

            invoices = sorted(invoices, key=lambda item: item.created_at or self._utcnow(), reverse=True)

            return {
                "current_plan": current_plan,
                "current_subscription": current_subscription,
                "invoices": invoices,
                "next_billing_date": cached_summary.get("next_billing_date"),
                "days_until_renewal": cached_summary.get("days_until_renewal"),
                "is_cancelled": bool(cached_summary.get("is_cancelled", False)),
                "total_spent_inr": float(cached_summary.get("total_spent_inr", 0)),
            }

        organization = db.session.get(Organization, org_uuid)
        if organization is None:
            raise BillingServiceError("Organization not found")

        current_plan = db.session.get(Plan, organization.plan_id) if organization.plan_id else None
        current_subscription = (
            Subscription.query.filter_by(org_id=organization.id, status="active")
            .order_by(Subscription.created_at.desc())
            .first()
        )
        invoices = (
            Invoice.query.filter_by(org_id=organization.id)
            .order_by(Invoice.created_at.desc())
            .limit(12)
            .all()
        )

        next_billing_date = None
        days_until_renewal = None
        is_cancelled = False
        now = self._utcnow()

        if current_subscription and current_subscription.current_period_end:
            next_billing_date = self._format_ist_datetime(current_subscription.current_period_end)
            period_end = current_subscription.current_period_end
            if period_end.tzinfo is None:
                period_end = period_end.replace(tzinfo=timezone.utc)
            days_until_renewal = max((period_end - now).days, 0)
            is_cancelled = bool(
                current_subscription.cancel_at and period_end > now
            )

        total_spent_paise = (
            db.session.query(func.coalesce(func.sum(Invoice.amount_paise), 0))
            .filter(
                Invoice.org_id == organization.id,
                Invoice.status == "paid",
            )
            .scalar()
            or 0
        )

        total_spent_inr = float(Decimal(total_spent_paise) / Decimal("100"))

        summary = {
            "current_plan": current_plan,
            "current_subscription": current_subscription,
            "invoices": invoices,
            "next_billing_date": next_billing_date,
            "days_until_renewal": days_until_renewal,
            "is_cancelled": is_cancelled,
            "total_spent_inr": total_spent_inr,
        }

        try:
            cache.set(
                cache_key,
                {
                    "current_plan_id": str(current_plan.id) if current_plan else None,
                    "current_subscription_id": (
                        str(current_subscription.id) if current_subscription else None
                    ),
                    "invoice_ids": [str(invoice.id) for invoice in invoices],
                    "next_billing_date": next_billing_date,
                    "days_until_renewal": days_until_renewal,
                    "is_cancelled": is_cancelled,
                    "total_spent_inr": total_spent_inr,
                },
                timeout=300,
            )
        except Exception as exc:  # pylint: disable=broad-except
            current_app.logger.warning("Failed to cache billing summary for org %s: %s", org_id, exc)

        return summary


billing_service = BillingService()
