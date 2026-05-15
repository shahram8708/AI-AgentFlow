"""Notification service layer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from flask import current_app

from app.extensions import cache, db
from app.models.notification import Notification


class NotificationService:
    """Service for creating and managing user notifications."""

    ALLOWED_TYPES = {
        "task_complete",
        "task_failed",
        "billing",
        "team",
        "system",
        "quota_warning",
    }

    @staticmethod
    def _count_cache_key(user_id: Any) -> str:
        return f"notification_count:{user_id}"

    def _bust_unread_count_cache(self, user_id: Any) -> None:
        """Invalidate unread count cache for a user."""

        try:
            cache.delete(self._count_cache_key(user_id))
        except Exception as exc:
            current_app.logger.error(
                "Failed to clear notification count cache for user %s: %s",
                user_id,
                exc,
            )

    def create_notification(
        self,
        user_id: Any,
        org_id: Any,
        notification_type: str,
        title: str,
        message: str,
        action_url: str | None = None,
    ) -> Notification | None:
        """Create and persist a notification entry."""

        if notification_type not in self.ALLOWED_TYPES:
            current_app.logger.error(
                "Invalid notification type '%s' for user %s",
                notification_type,
                user_id,
            )
            return None

        try:
            notification = Notification(
                user_id=user_id,
                org_id=org_id,
                type=notification_type,
                title=title,
                message=message,
                action_url=action_url,
                is_read=False,
            )
            db.session.add(notification)
            db.session.commit()
            self._bust_unread_count_cache(user_id)
            return notification
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error(
                "Failed to create notification for user %s: %s",
                user_id,
                exc,
            )
            return None

    def mark_as_read(self, notification_id: Any, user_id: Any) -> bool:
        """Mark a specific user owned notification as read."""

        try:
            notification = Notification.query.filter_by(
                id=notification_id,
                user_id=user_id,
                is_deleted=False,
            ).first()
            if notification is None:
                return False

            notification.is_read = True
            db.session.commit()
            self._bust_unread_count_cache(user_id)
            return True
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error(
                "Failed to mark notification %s as read for user %s: %s",
                notification_id,
                user_id,
                exc,
            )
            return False

    def mark_all_as_read(self, user_id: Any, org_id: Any) -> int:
        """Mark all unread notifications for a user and organization as read."""

        try:
            updated_count = Notification.query.filter_by(
                user_id=user_id,
                org_id=org_id,
                is_read=False,
                is_deleted=False,
            ).update({"is_read": True}, synchronize_session=False)
            db.session.commit()
            self._bust_unread_count_cache(user_id)
            return int(updated_count)
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error(
                "Failed to mark all notifications as read for user %s org %s: %s",
                user_id,
                org_id,
                exc,
            )
            return 0

    def get_notifications(
        self,
        user_id: Any,
        org_id: Any,
        page: int = 1,
        per_page: int = 20,
        notification_type: str | None = None,
        unread_only: bool = False,
    ):
        """Get paginated notifications for a user in an organization."""

        try:
            query = Notification.query.filter_by(
                user_id=user_id,
                org_id=org_id,
                is_deleted=False,
            )

            if notification_type:
                query = query.filter(Notification.type == notification_type)
            if unread_only:
                query = query.filter(Notification.is_read.is_(False))

            return query.order_by(Notification.created_at.desc()).paginate(
                page=max(int(page), 1),
                per_page=max(int(per_page), 1),
                error_out=False,
            )
        except Exception as exc:
            current_app.logger.error(
                "Failed to fetch notifications for user %s org %s: %s",
                user_id,
                org_id,
                exc,
            )
            return Notification.query.filter(Notification.id.is_(None)).paginate(
                page=max(int(page), 1),
                per_page=max(int(per_page), 1),
                error_out=False,
            )

    def get_unread_count(self, user_id: Any, org_id: Any) -> int:
        """Return unread notification count with 60 second cache."""

        if user_id is None or org_id is None:
            return 0

        cache_key = self._count_cache_key(user_id)
        try:
            cached_value = cache.get(cache_key)
            if cached_value is not None:
                return int(cached_value)

            count = (
                Notification.query.filter_by(
                    user_id=user_id,
                    org_id=org_id,
                    is_read=False,
                    is_deleted=False,
                ).count()
            )
            cache.set(cache_key, int(count), timeout=60)
            return int(count)
        except Exception as exc:
            current_app.logger.error(
                "Failed to get unread notification count for user %s: %s",
                user_id,
                exc,
            )
            return 0

    def delete_notification(self, notification_id: Any, user_id: Any) -> bool:
        """Soft delete a notification owned by the user."""

        try:
            notification = Notification.query.filter_by(
                id=notification_id,
                user_id=user_id,
                is_deleted=False,
            ).first()
            if notification is None:
                return False

            notification.is_deleted = True
            notification.deleted_at = datetime.now(timezone.utc)
            db.session.commit()
            self._bust_unread_count_cache(user_id)
            return True
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error(
                "Failed to delete notification %s for user %s: %s",
                notification_id,
                user_id,
                exc,
            )
            return False

    def notify_task_complete(self, task: Any) -> Notification | None:
        """Create task completion notification for a task owner."""

        return self.create_notification(
            user_id=task.user_id,
            org_id=task.org_id,
            notification_type="task_complete",
            title="Task Complete",
            message=f'Your task "{task.task_name}" completed successfully.',
            action_url=f"/tasks/{task.id}",
        )

    def notify_task_failed(self, task: Any, error_message: str) -> Notification | None:
        """Create task failed notification for a task owner."""

        safe_error = (error_message or "Unknown error")[:100]
        return self.create_notification(
            user_id=task.user_id,
            org_id=task.org_id,
            notification_type="task_failed",
            title="Task Failed",
            message=f'Your task "{task.task_name}" failed: {safe_error}',
            action_url=f"/tasks/{task.id}",
        )

    def notify_quota_warning(
        self,
        user_id: Any,
        org_id: Any,
        used: int,
        quota: int,
    ) -> Notification | None:
        """Notify user that usage is approaching monthly task limit."""

        return self.create_notification(
            user_id=user_id,
            org_id=org_id,
            notification_type="quota_warning",
            title="Approaching Task Limit",
            message=(
                f"You have used {used} of {quota} tasks this month. "
                "Upgrade to continue without interruption."
            ),
            action_url="/settings/billing",
        )

    def notify_team_invitation(
        self,
        invited_user_id: Any,
        org_id: Any,
        invited_by_name: str,
        org_name: str,
    ) -> Notification | None:
        """Notify invited users about team invitations."""

        return self.create_notification(
            user_id=invited_user_id,
            org_id=org_id,
            notification_type="team",
            title="Team Invitation",
            message=f"{invited_by_name} invited you to join {org_name} on AgentFlow.",
            action_url="/team",
        )

    def notify_billing_event(
        self,
        org_id: Any,
        owner_user_id: Any,
        event_type: str,
        plan_name: str,
    ) -> Notification | None:
        """Notify organization owner about billing events."""

        title = "Billing Update"
        message = f"A billing update occurred on your {plan_name} plan."

        if event_type == "upgraded":
            title = "Plan Upgraded"
            message = f"Your plan has been upgraded to {plan_name}."
        elif event_type == "cancelled":
            title = "Subscription Cancelled"
            message = "Your subscription has been cancelled."
        elif event_type == "payment_failed":
            title = "Payment Failed"
            message = "Your last payment failed. Please update your payment method."

        return self.create_notification(
            user_id=owner_user_id,
            org_id=org_id,
            notification_type="billing",
            title=title,
            message=message,
            action_url="/settings/billing",
        )

    def create(
        self,
        user_id: Any,
        org_id: Any,
        notification_type: str,
        title: str,
        message: str,
        action_url: str | None = None,
    ) -> Notification | None:
        """Backward compatible alias to create notification."""

        return self.create_notification(
            user_id=user_id,
            org_id=org_id,
            notification_type=notification_type,
            title=title,
            message=message,
            action_url=action_url,
        )

    def mark_read(self, notification: Notification) -> Notification | None:
        """Backward compatible alias to mark notification as read."""

        if notification is None:
            return None
        if self.mark_as_read(notification.id, notification.user_id):
            return notification
        return None
