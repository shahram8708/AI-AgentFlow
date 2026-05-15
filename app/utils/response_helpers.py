"""JSON response helper functions."""

from __future__ import annotations

from typing import Any

from flask import jsonify


def success_response(
    data: Any = None, message: str = "", status: int = 200
):
    """Return a standard success JSON response."""

    return jsonify({"success": True, "data": data, "message": message}), status


def error_response(
    message: str, status: int = 400, errors: Any = None
):
    """Return a standard error JSON response."""

    return jsonify({"success": False, "message": message, "errors": errors}), status


def paginated_response(items: list[Any], pagination: Any):
    """Return a standard paginated JSON response."""

    return jsonify(
        {
            "success": True,
            "data": {
                "items": items,
                "total": pagination.total,
                "page": pagination.page,
                "pages": pagination.pages,
                "per_page": pagination.per_page,
            },
            "message": "",
        }
    )
