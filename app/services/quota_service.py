"""Quota calculation service."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func

from app.models.usage import UsageRecord


class QuotaService:
    """Service for usage and quota checks."""

    def month_usage(self, org_id: str) -> int:
        """Return current month task usage units for organization."""

        now_utc = datetime.now(timezone.utc)
        period_start = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        usage = (
            UsageRecord.query.with_entities(
                func.coalesce(func.sum(UsageRecord.units_consumed), 0)
            )
            .filter(
                UsageRecord.org_id == org_id,
                UsageRecord.usage_type == "task_run",
                UsageRecord.recorded_at >= period_start,
            )
            .scalar()
        )
        return int(usage or 0)
