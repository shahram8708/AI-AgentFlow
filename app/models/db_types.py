"""Database type aliases that work across PostgreSQL and SQLite."""

from __future__ import annotations

from sqlalchemy import JSON, Uuid
from sqlalchemy.dialects.postgresql import JSONB as PostgresJSONB

UUID = Uuid
JSONB = JSON().with_variant(PostgresJSONB, "postgresql")