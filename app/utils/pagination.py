"""Pagination helpers."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any, Iterable


@dataclass
class Pagination:
    """Lightweight pagination metadata helper."""

    page: int
    per_page: int
    total: int
    items: Iterable[Any]

    @property
    def pages(self) -> int:
        """Return total page count."""

        if self.per_page <= 0:
            return 0
        return ceil(self.total / self.per_page)


def paginate_query(query: Any, page: int, per_page: int = 25) -> Any:
    """Paginate a Flask SQLAlchemy query and return native paginate result."""

    safe_page = max(page, 1)
    safe_per_page = max(1, min(per_page, 100))
    return query.paginate(page=safe_page, per_page=safe_per_page, error_out=False)
