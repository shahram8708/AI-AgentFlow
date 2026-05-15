"""Billing blueprint routes."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytz
from flask import Blueprint, current_app, flash, g, jsonify, request, send_file, session
from flask_login import current_user

from app.extensions import csrf, db
from app.models import AuditLog, Invoice, OrganizationMember, Plan, Subscription
from app.services.billing_service import BillingServiceError, PaymentVerificationError, billing_service
from app.services.email_service import EmailService
from app.services.notification_service import NotificationService
from app.utils.decorators import login_required, org_required
from app.utils.response_helpers import error_response, success_response
from app.utils.validators import validate_uuid

billing_bp = Blueprint("billing", __name__)

notification_service = NotificationService()
email_service = EmailService()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_ist_string(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    ist = pytz.timezone("Asia/Kolkata")
    return value.astimezone(ist).strftime("%d %b %Y, %I:%M %p IST")


def _current_org_role() -> str:
    membership = OrganizationMember.query.filter_by(org_id=g.org.id, user_id=current_user.id).first()
    return membership.role if membership else "viewer"


@billing_bp.post("/checkout")
@login_required
@org_required
def checkout():
    """Create Razorpay checkout order for selected plan and billing cycle."""

    payload = request.get_json(silent=True) or {}
    plan_id = str(payload.get("plan_id") or "").strip()
    billing_cycle = str(payload.get("billing_cycle") or "").strip().lower()

    if not plan_id or billing_cycle not in {"monthly", "annual"}:
        return error_response("Please provide a valid plan and billing cycle.", 400)

    if not validate_uuid(plan_id):
        return error_response("Invalid plan selected.", 400)

    plan = Plan.query.filter_by(id=UUID(plan_id), is_active=True).first()
    if plan is None:
        return error_response("Plan not found.", 404)

    if plan.price_monthly_inr == -1 or plan.price_annual_inr == -1:
        return error_response(
            "Enterprise plans require contacting sales. Please use the Contact Sales form.",
            400,
        )

    amount_inr = (
        float(Decimal(plan.price_monthly_inr) / Decimal("100"))
        if billing_cycle == "monthly"
        else float(Decimal(plan.price_annual_inr) / Decimal("100"))
    )

    try:
        order = billing_service.create_order(
            amount_inr=amount_inr,
            org_id=str(g.org.id),
            plan_id=plan_id,
            billing_cycle=billing_cycle,
        )
    except BillingServiceError as exc:
        current_app.logger.error("Checkout order creation failed: %s", exc)
        return error_response("Unable to start checkout right now. Please try again.", 500)

    session["pending_order"] = {
        "order_id": order.get("id"),
        "plan_id": plan_id,
        "billing_cycle": billing_cycle,
        "amount_paise": order.get("amount"),
    }

    return success_response(
        {
            "order_id": order.get("id"),
            "amount": order.get("amount"),
            "currency": "INR",
            "key_id": os.environ.get("RAZORPAY_KEY_ID"),
            "org_name": g.org.name,
            "plan_name": plan.name,
        }
    )


@billing_bp.post("/verify")
@login_required
@org_required
def verify_payment():
    """Verify Razorpay payment signature and activate subscription."""

    payload = request.get_json(silent=True) or {}
    razorpay_order_id = str(payload.get("razorpay_order_id") or "").strip()
    razorpay_payment_id = str(payload.get("razorpay_payment_id") or "").strip()
    razorpay_signature = str(payload.get("razorpay_signature") or "").strip()

    if not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
        return error_response("Missing payment verification data.", 400)

    pending_order = session.get("pending_order")
    if not isinstance(pending_order, dict):
        return error_response("Invalid payment session. Please try again.", 400)

    if str(pending_order.get("order_id")) != razorpay_order_id:
        return error_response("Invalid payment session. Please try again.", 400)

    try:
        is_valid = billing_service.verify_payment_signature(
            razorpay_order_id=razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature=razorpay_signature,
        )
        if not is_valid:
            db.session.add(
                AuditLog(
                    org_id=g.org.id,
                    user_id=current_user.id,
                    action="billing.payment_verification_failed",
                    resource_type="billing_payment",
                    resource_id=razorpay_payment_id,
                    ip_address=request.remote_addr,
                    user_agent=(request.user_agent.string or "")[:500],
                    extra_json={"order_id": razorpay_order_id},
                )
            )
            db.session.commit()
            return error_response("Payment verification failed. Please contact support.", 400)

        existing_invoice = Invoice.query.filter_by(
            org_id=g.org.id,
            razorpay_payment_id=razorpay_payment_id,
        ).first()
        if existing_invoice is not None:
            session.pop("pending_order", None)
            existing_plan_name = (
                existing_invoice.subscription.plan.name
                if existing_invoice.subscription and existing_invoice.subscription.plan
                else "Current"
            )
            return success_response(
                {
                    "message": f"Payment already verified for {existing_plan_name}.",
                    "plan_name": existing_plan_name,
                    "redirect": "/settings/billing",
                }
            )

        subscription = billing_service.activate_subscription(
            org_id=str(g.org.id),
            plan_id=str(pending_order.get("plan_id")),
            razorpay_payment_id=razorpay_payment_id,
            razorpay_order_id=razorpay_order_id,
            billing_cycle=str(pending_order.get("billing_cycle") or "monthly"),
        )

        session.pop("pending_order", None)

        plan = db.session.get(Plan, subscription.plan_id)
        plan_name = plan.name if plan else "Selected"

        notification_service.notify_billing_event(
            org_id=g.org.id,
            owner_user_id=g.org.owner_id,
            event_type="upgraded",
            plan_name=plan_name,
        )

        invoice = (
            Invoice.query.filter_by(org_id=g.org.id, razorpay_payment_id=razorpay_payment_id)
            .order_by(Invoice.created_at.desc())
            .first()
        )
        amount_display = float(Decimal((pending_order.get("amount_paise") or 0)) / Decimal("100"))
        invoice_id = str(invoice.id) if invoice else ""

        email_service.send_generic_email(
            current_user.email,
            f"Payment confirmed - Welcome to {plan_name}!",
            "emails/invoice.html",
            {
                "plan_name": plan_name,
                "billing_cycle": str(pending_order.get("billing_cycle") or "monthly"),
                "amount_display": f"{amount_display:,.2f}",
                "payment_date": _to_ist_string(_utcnow()),
                "payment_id": razorpay_payment_id,
                "payment_id_short": razorpay_payment_id[-10:],
                "invoice_id": invoice_id,
            },
        )

        db.session.add(
            AuditLog(
                org_id=g.org.id,
                user_id=current_user.id,
                action="billing.payment_verified",
                resource_type="subscription",
                resource_id=str(subscription.id),
                ip_address=request.remote_addr,
                user_agent=(request.user_agent.string or "")[:500],
                extra_json={
                    "plan_name": plan_name,
                    "billing_cycle": subscription.billing_cycle,
                    "payment_id": razorpay_payment_id,
                },
            )
        )
        db.session.commit()

        return success_response(
            {
                "message": f"Successfully upgraded to {plan_name}!",
                "plan_name": plan_name,
                "redirect": "/settings/billing",
            }
        )
    except PaymentVerificationError:
        return error_response("Payment verification failed. Please contact support.", 400)
    except BillingServiceError as exc:
        current_app.logger.error("Billing verification flow failed: %s", exc)
        return error_response("Unable to process payment right now. Please contact support.", 500)
    except Exception as exc:  # pylint: disable=broad-except
        db.session.rollback()
        current_app.logger.error("Unexpected payment verification error: %s", exc)
        return error_response("Unable to verify payment right now. Please contact support.", 500)


@billing_bp.post("/webhook")
@csrf.exempt
def webhook():
    """Receive and process Razorpay webhook events."""

    raw_body = request.get_data()
    webhook_signature = request.headers.get("X-Razorpay-Signature", "")

    is_valid = billing_service.verify_webhook_signature(raw_body, webhook_signature)
    if not is_valid:
        current_app.logger.warning(
            "Webhook signature verification failed from %s",
            request.remote_addr,
        )
        return jsonify({"error": "Invalid signature"}), 400

    try:
        event_data = json.loads(raw_body or b"{}")
    except json.JSONDecodeError:
        current_app.logger.warning("Invalid webhook JSON payload received")
        return jsonify({"error": "Invalid payload"}), 400

    event_type = event_data.get("event")
    payload = event_data.get("payload", {})

    try:
        billing_service.handle_webhook_event(event_type, payload)
    except Exception as exc:  # pylint: disable=broad-except
        current_app.logger.error("Webhook handling error for %s: %s", event_type, exc)

    return jsonify({"status": "ok"}), 200


@billing_bp.get("/invoices/<invoice_id>/download")
@login_required
@org_required
def download_invoice(invoice_id: str):
    """Generate and stream invoice PDF download for current org."""

    if not validate_uuid(invoice_id):
        return error_response("Invalid invoice ID.", 400)

    invoice = Invoice.query.filter_by(id=UUID(invoice_id), org_id=g.org.id).first()
    if invoice is None:
        return error_response("Invoice not found.", 404)

    try:
        buffer = billing_service.get_invoice_pdf(invoice_id, str(g.org.id))
    except BillingServiceError as exc:
        current_app.logger.error("Invoice PDF generation failed for %s: %s", invoice_id, exc)
        return error_response("Unable to generate invoice PDF right now.", 500)

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"AgentFlow_Invoice_{invoice_id[:8].upper()}.pdf",
    )


@billing_bp.post("/cancel")
@login_required
@org_required
def cancel_subscription():
    """Cancel active subscription with strict owner confirmation."""

    role = _current_org_role()
    if role != "owner":
        return error_response("Only the organization owner can cancel the subscription.", 403)

    payload = request.get_json(silent=True) or {}
    confirmation = str(payload.get("confirmation") or "").strip()
    if confirmation != "CANCEL":
        return error_response("Please confirm cancellation by sending 'CANCEL'", 400)

    reason = str(payload.get("reason") or "User requested cancellation").strip()

    try:
        billing_service.cancel_subscription(str(g.org.id), reason=reason)
    except BillingServiceError as exc:
        return error_response(str(exc), 400)

    subscription = (
        Subscription.query.filter_by(org_id=g.org.id)
        .order_by(Subscription.updated_at.desc())
        .first()
    )
    cancel_at_text = _to_ist_string(subscription.cancel_at if subscription else None)
    message = (
        f"Subscription cancelled. You'll have access until {cancel_at_text}."
        if cancel_at_text
        else "Subscription cancelled successfully."
    )

    db.session.add(
        AuditLog(
            org_id=g.org.id,
            user_id=current_user.id,
            action="billing.cancellation_requested",
            resource_type="subscription",
            resource_id=str(subscription.id) if subscription else None,
            ip_address=request.remote_addr,
            user_agent=(request.user_agent.string or "")[:500],
            extra_json={"reason": reason},
        )
    )
    db.session.commit()

    flash(message, "warning")
    return success_response({"message": message, "redirect": "/settings/billing"})
