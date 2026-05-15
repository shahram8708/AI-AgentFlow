"""Team management blueprint routes."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.forms.team import InviteMemberForm, UpdateRoleForm
from app.models import AuditLog, OrganizationMember, Plan, User
from app.services.email_service import EmailService
from app.services.notification_service import NotificationService
from app.utils.decorators import login_required, org_required
from app.utils.response_helpers import error_response, success_response
from app.utils.validators import validate_uuid

team_bp = Blueprint("team", __name__)

email_service = EmailService()
notification_service = NotificationService()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_json_request() -> bool:
    requested_with = (request.headers.get("X-Requested-With") or "").lower().strip()
    return (
        request.is_json
        or request.accept_mimetypes.best == "application/json"
        or requested_with == "xmlhttprequest"
    )


def _get_current_membership() -> OrganizationMember | None:
    return OrganizationMember.query.filter_by(org_id=g.org.id, user_id=current_user.id).first()


def _generate_invitation_token(member: OrganizationMember) -> str:
    secret_key = str(current_app.config.get("SECRET_KEY") or "")
    token_data = f"{str(member.id)}{member.invited_at.isoformat() if member.invited_at else ''}"
    return hmac.new(
        secret_key.encode("utf-8"),
        token_data.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]


def _resolve_invitation_member(token: str) -> OrganizationMember | None:
    if not token or len(token) != 32:
        return None

    cutoff = _utcnow() - timedelta(days=7)
    pending_members = (
        OrganizationMember.query.options(
            joinedload(OrganizationMember.user),
            joinedload(OrganizationMember.organization),
        )
        .filter(
            OrganizationMember.joined_at.is_(None),
            OrganizationMember.invited_at.isnot(None),
            OrganizationMember.invited_at >= cutoff,
        )
        .all()
    )

    for member in pending_members:
        expected_token = _generate_invitation_token(member)
        if hmac.compare_digest(expected_token, token):
            return member

    return None


def _member_initials(user: User) -> str:
    first = (user.first_name or "")[:1].upper()
    last = (user.last_name or "")[:1].upper()
    initials = f"{first}{last}".strip()
    return initials or "AF"


def _avatar_color(user_id: Any) -> str:
    palette = ["#1D4ED8", "#0EA5E9", "#14B8A6", "#F97316", "#EF4444", "#7C3AED"]
    try:
        index = int(getattr(user_id, "int", 0)) % len(palette)
    except Exception:  # pylint: disable=broad-except
        index = 0
    return palette[index]


def _decorate_member(member: OrganizationMember) -> dict[str, Any]:
    user = member.user
    now = _utcnow()
    status = "never"
    if user and user.last_login:
        last_login = user.last_login
        if last_login.tzinfo is None:
            last_login = last_login.replace(tzinfo=timezone.utc)
        status = "active" if (now - last_login) <= timedelta(days=30) else "inactive"

    return {
        "membership": member,
        "user": user,
        "initials": _member_initials(user) if user else "AF",
        "avatar_color": _avatar_color(user.id if user else None),
        "status": status,
        "is_current_user": bool(user and user.id == current_user.id),
        "is_owner": member.role == "owner" or bool(user and user.id == g.org.owner_id),
    }


@team_bp.get("/team")
@login_required
@org_required
def team_home():
    """Render team management page with active members and pending invitations."""

    page = request.args.get("page", default=1, type=int)
    page = max(page, 1)

    pagination = (
        OrganizationMember.query.options(joinedload(OrganizationMember.user))
        .filter(OrganizationMember.org_id == g.org.id)
        .order_by(OrganizationMember.created_at.asc())
        .paginate(page=page, per_page=50, error_out=False)
    )

    memberships = pagination.items
    active_members = [_decorate_member(member) for member in memberships if member.joined_at is not None]
    pending_invitations = [
        {
            **_decorate_member(member),
            "expires_at": (
                (member.invited_at + timedelta(days=7)) if member.invited_at else None
            ),
        }
        for member in memberships
        if member.joined_at is None
    ]

    current_membership = _get_current_membership()
    current_role = current_membership.role if current_membership else "viewer"
    can_manage = current_role in {"owner", "admin"}

    plan = db.session.get(Plan, g.org.plan_id) if g.org.plan_id else None
    active_count = (
        OrganizationMember.query.filter(
            OrganizationMember.org_id == g.org.id,
            OrganizationMember.joined_at.isnot(None),
        ).count()
    )

    invite_form = InviteMemberForm()
    role_form = UpdateRoleForm()

    return render_template(
        "app/team.html",
        active_members=active_members,
        pending_invitations=pending_invitations,
        can_manage=can_manage,
        current_user_role=current_role,
        invite_form=invite_form,
        role_form=role_form,
        plan=plan,
        seat_limit=plan.seat_limit if plan else 1,
        seats_used=int(g.org.seats_used or 0),
        active_count=active_count,
        pagination=pagination,
    )


@team_bp.post("/team/invite")
@login_required
@org_required
def invite_member():
    """Invite a user to join the current organization."""

    actor_membership = _get_current_membership()
    if actor_membership is None or actor_membership.role not in {"owner", "admin"}:
        abort(403)

    plan = db.session.get(Plan, g.org.plan_id) if g.org.plan_id else None
    seat_limit = plan.seat_limit if plan else 1
    seats_used = int(g.org.seats_used or 0)

    if seat_limit != -1 and seats_used >= seat_limit:
        return error_response(
            "You have reached the seat limit for your current plan. Please upgrade to add more team members.",
            403,
        )

    payload = request.get_json(silent=True) if request.is_json else None
    if isinstance(payload, dict):
        form = InviteMemberForm(meta={"csrf": False}, data=payload)
        is_valid = form.validate()
    else:
        form = InviteMemberForm()
        is_valid = form.validate_on_submit()

    if not is_valid:
        if _is_json_request():
            return error_response("Please provide a valid email and role.", 400, form.errors)
        flash("Please provide a valid email and role.", "danger")
        return redirect(url_for("team.team_home"))

    normalized_email = (form.email.data or "").strip().lower()
    role = (form.role.data or "member").strip().lower()

    existing_member = (
        OrganizationMember.query.join(User, OrganizationMember.user_id == User.id)
        .filter(
            OrganizationMember.org_id == g.org.id,
            func.lower(User.email) == normalized_email,
        )
        .first()
    )
    if existing_member:
        return error_response("This user is already a member of your organization.", 400)

    user = User.query.filter(func.lower(User.email) == normalized_email).first()
    had_existing_account = user is not None

    now = _utcnow()

    try:
        if user is None:
            local_name = normalized_email.split("@", 1)[0]
            first_name = (local_name[:60] or "Invited").replace(".", " ").replace("_", " ").title()
            user = User(
                email=normalized_email,
                first_name=first_name or "Invited",
                last_name="User",
                is_verified=False,
                is_active=False,
                role="user",
            )
            user.set_password(secrets.token_urlsafe(32))
            db.session.add(user)
            db.session.flush()

        membership = OrganizationMember(
            org_id=g.org.id,
            user_id=user.id,
            role=role,
            invited_at=now,
            joined_at=None,
        )
        db.session.add(membership)

        g.org.seats_used = max(0, int(g.org.seats_used or 0) + 1)

        db.session.flush()

        email_service.send_team_invitation_email(user, current_user, g.org, role)

        if had_existing_account:
            notification_service.notify_team_invitation(
                user.id,
                g.org.id,
                current_user.get_full_name(),
                g.org.name,
            )

        db.session.add(
            AuditLog(
                org_id=g.org.id,
                user_id=current_user.id,
                action="team.member_invited",
                resource_type="organization_member",
                resource_id=str(membership.id),
                ip_address=request.remote_addr,
                user_agent=(request.user_agent.string or "")[:500],
                extra_json={
                    "invited_email": normalized_email,
                    "role": role,
                },
            )
        )

        db.session.commit()

        if _is_json_request():
            return success_response(
                {
                    "message": f"Invitation sent to {normalized_email}",
                    "member_id": str(membership.id),
                    "email": normalized_email,
                    "role": role,
                    "invited_at": membership.invited_at.isoformat() if membership.invited_at else None,
                }
            )

        flash(f"Invitation sent to {normalized_email}", "success")
        return redirect(url_for("team.team_home"))
    except Exception as exc:  # pylint: disable=broad-except
        db.session.rollback()
        current_app.logger.error("Failed to invite team member %s: %s", normalized_email, exc)
        return error_response("Unable to send invitation right now. Please try again.", 500)


@team_bp.put("/team/<member_id>/role")
@login_required
@org_required
def update_member_role(member_id: str):
    """Update organization member role with strict owner protections."""

    actor_membership = _get_current_membership()
    if actor_membership is None or actor_membership.role not in {"owner", "admin"}:
        abort(403)

    if not validate_uuid(member_id):
        return error_response("Invalid member ID", 400)

    member = OrganizationMember.query.filter_by(id=UUID(member_id), org_id=g.org.id).first()
    if member is None:
        return error_response("Member not found", 404)

    if member.role == "owner" or member.user_id == g.org.owner_id:
        return error_response("The organization owner role cannot be changed.", 400)

    payload = request.get_json(silent=True) or request.form.to_dict(flat=True)
    new_role = str(payload.get("role") or "").strip().lower()

    if new_role not in {"admin", "member", "viewer"}:
        return error_response("Invalid role selected.", 400)

    if new_role == "owner":
        return error_response("Owner role cannot be assigned from this endpoint.", 400)

    old_role = member.role
    member.role = new_role

    db.session.add(
        AuditLog(
            org_id=g.org.id,
            user_id=current_user.id,
            action="team.role_changed",
            resource_type="organization_member",
            resource_id=str(member.id),
            ip_address=request.remote_addr,
            user_agent=(request.user_agent.string or "")[:500],
            extra_json={
                "member_id": member_id,
                "old_role": old_role,
                "new_role": new_role,
            },
        )
    )

    db.session.commit()
    return success_response({"role": new_role})


@team_bp.delete("/team/<member_id>")
@login_required
@org_required
def remove_member(member_id: str):
    """Remove organization member from team."""

    actor_membership = _get_current_membership()
    if actor_membership is None or actor_membership.role not in {"owner", "admin"}:
        abort(403)

    if not validate_uuid(member_id):
        return error_response("Invalid member ID", 400)

    member = OrganizationMember.query.filter_by(id=UUID(member_id), org_id=g.org.id).first()
    if member is None:
        return error_response("Member not found", 404)

    if member.role == "owner" or member.user_id == g.org.owner_id:
        return error_response("The organization owner cannot be removed.", 400)

    if member.user_id == current_user.id:
        return error_response("You cannot remove yourself. Use account deletion settings instead.", 400)

    try:
        db.session.delete(member)
        g.org.seats_used = max(0, int(g.org.seats_used or 0) - 1)

        db.session.add(
            AuditLog(
                org_id=g.org.id,
                user_id=current_user.id,
                action="team.member_removed",
                resource_type="organization_member",
                resource_id=member_id,
                ip_address=request.remote_addr,
                user_agent=(request.user_agent.string or "")[:500],
            )
        )

        db.session.commit()
        return success_response({"removed": True})
    except Exception as exc:  # pylint: disable=broad-except
        db.session.rollback()
        current_app.logger.error("Failed to remove team member %s: %s", member_id, exc)
        return error_response("Unable to remove member right now.", 500)


@team_bp.delete("/team/invitations/<member_id>")
@login_required
@org_required
def cancel_invitation(member_id: str):
    """Cancel a pending team invitation."""

    actor_membership = _get_current_membership()
    if actor_membership is None or actor_membership.role not in {"owner", "admin"}:
        abort(403)

    if not validate_uuid(member_id):
        return error_response("Invalid invitation ID", 400)

    member = OrganizationMember.query.filter_by(id=UUID(member_id), org_id=g.org.id).first()
    if member is None:
        return error_response("Invitation not found", 404)

    if member.joined_at is not None:
        return error_response("This invitation has already been accepted.", 400)

    try:
        db.session.delete(member)
        g.org.seats_used = max(0, int(g.org.seats_used or 0) - 1)

        db.session.add(
            AuditLog(
                org_id=g.org.id,
                user_id=current_user.id,
                action="team.invitation_cancelled",
                resource_type="organization_member",
                resource_id=member_id,
                ip_address=request.remote_addr,
                user_agent=(request.user_agent.string or "")[:500],
            )
        )

        db.session.commit()
        return success_response({"cancelled": True})
    except Exception as exc:  # pylint: disable=broad-except
        db.session.rollback()
        current_app.logger.error("Failed to cancel invitation %s: %s", member_id, exc)
        return error_response("Unable to cancel invitation right now.", 500)


@team_bp.post("/team/invitations/<member_id>/resend")
@login_required
@org_required
def resend_invitation(member_id: str):
    """Resend invitation email for pending member with one hour cooldown."""

    actor_membership = _get_current_membership()
    if actor_membership is None or actor_membership.role not in {"owner", "admin"}:
        abort(403)

    if not validate_uuid(member_id):
        return error_response("Invalid invitation ID", 400)

    member = (
        OrganizationMember.query.options(joinedload(OrganizationMember.user))
        .filter_by(id=UUID(member_id), org_id=g.org.id)
        .first()
    )
    if member is None:
        return error_response("Invitation not found", 404)

    if member.joined_at is not None:
        return error_response("This invitation has already been accepted.", 400)

    now = _utcnow()
    invited_at = member.invited_at
    if invited_at is not None:
        if invited_at.tzinfo is None:
            invited_at = invited_at.replace(tzinfo=timezone.utc)
        if (now - invited_at) < timedelta(hours=1):
            return error_response("You can resend this invitation once every hour.", 429)

    try:
        email_service.send_team_invitation_email(member.user, current_user, g.org, member.role)
        member.invited_at = now

        db.session.add(
            AuditLog(
                org_id=g.org.id,
                user_id=current_user.id,
                action="team.invitation_resent",
                resource_type="organization_member",
                resource_id=member_id,
                ip_address=request.remote_addr,
                user_agent=(request.user_agent.string or "")[:500],
            )
        )

        db.session.commit()
        return success_response({"resent": True})
    except Exception as exc:  # pylint: disable=broad-except
        db.session.rollback()
        current_app.logger.error("Failed to resend invitation %s: %s", member_id, exc)
        return error_response("Unable to resend invitation right now.", 500)


@team_bp.get("/team/accept-invitation/<token>")
def accept_invitation(token: str):
    """Accept pending invitation using secure HMAC token."""

    member = _resolve_invitation_member(token)
    if member is None:
        return render_template("errors/404.html"), 404

    invited_at = member.invited_at
    if invited_at is None:
        return render_template("errors/400.html"), 400

    if invited_at.tzinfo is None:
        invited_at = invited_at.replace(tzinfo=timezone.utc)
    if _utcnow() - invited_at > timedelta(days=7):
        return render_template("errors/400.html"), 400

    invited_user = member.user
    organization = member.organization

    if invited_user is None or organization is None:
        return render_template("errors/404.html"), 404

    if not invited_user.is_verified or not invited_user.is_active:
        return redirect(
            url_for(
                "auth.register",
                invitation_token=token,
                email=invited_user.email,
            )
        )

    if not current_user.is_authenticated:
        return redirect(url_for("auth.login", next=request.path))

    if current_user.id != invited_user.id:
        flash("This invitation is linked to a different account email.", "danger")
        return redirect("/dashboard")

    if member.joined_at is None:
        try:
            member.joined_at = _utcnow()
            invited_user.is_active = True

            db.session.add(
                AuditLog(
                    org_id=organization.id,
                    user_id=invited_user.id,
                    action="team.invitation_accepted",
                    resource_type="organization_member",
                    resource_id=str(member.id),
                    ip_address=request.remote_addr,
                    user_agent=(request.user_agent.string or "")[:500],
                )
            )

            db.session.commit()
        except Exception as exc:  # pylint: disable=broad-except
            db.session.rollback()
            current_app.logger.error("Failed to accept invitation for member %s: %s", member.id, exc)
            flash("Unable to accept invitation right now. Please try again.", "danger")
            return redirect("/dashboard")

    flash(f"You've joined {organization.name}!", "success")
    return redirect("/dashboard")
