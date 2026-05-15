"""Validation helper utilities."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from email_validator import EmailNotValidError, validate_email as _validate_email


def validate_email(email: str) -> bool:
    """Validate an email address."""

    try:
        _validate_email(email, check_deliverability=False)
        return True
    except EmailNotValidError:
        return False


def validate_password_strength(password: str) -> dict[str, object]:
    """Validate password strength and return score and failed rules."""

    rules = {
        "min_length": len(password) >= 8,
        "uppercase": bool(re.search(r"[A-Z]", password)),
        "lowercase": bool(re.search(r"[a-z]", password)),
        "digit": bool(re.search(r"\d", password)),
        "special": bool(re.search(r"[^A-Za-z0-9]", password)),
    }
    failed_rules = [rule for rule, passed in rules.items() if not passed]
    score = sum(1 for passed in rules.values() if passed)
    return {"score": score, "failed_rules": failed_rules}


def validate_uuid(value: str) -> bool:
    """Validate UUID value."""

    try:
        UUID(value)
        return True
    except (ValueError, TypeError):
        return False


def validate_file_extension(filename: str, allowed_extensions: set[str]) -> bool:
    """Validate file extension against allowed set."""

    if "." not in filename:
        return False
    extension = filename.rsplit(".", 1)[1].lower()
    allowed = {ext.lower().lstrip(".") for ext in allowed_extensions}
    return extension in allowed


def validate_cron_expression(expr: str) -> bool:
    """Validate standard five field cron expression."""

    parts = expr.strip().split()
    if len(parts) != 5:
        return False

    token_re = re.compile(r"^(\*|\d+|\d+\-\d+|\*/\d+|\d+,\d+(,\d+)*)$")
    ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]

    for idx, part in enumerate(parts):
        if not token_re.match(part):
            return False
        if part == "*":
            continue

        values = [part]
        if "," in part:
            values = part.split(",")

        for value in values:
            if value.startswith("*/"):
                step = value[2:]
                if not step.isdigit() or int(step) <= 0:
                    return False
                continue

            if "-" in value:
                left, right = value.split("-", 1)
                if not left.isdigit() or not right.isdigit():
                    return False
                left_int = int(left)
                right_int = int(right)
                if left_int > right_int:
                    return False
                min_range, max_range = ranges[idx]
                if left_int < min_range or right_int > max_range:
                    return False
                continue

            if not value.isdigit():
                return False

            min_range, max_range = ranges[idx]
            int_val = int(value)
            if int_val < min_range or int_val > max_range:
                return False

    return True


def validate_task_type(task_type: str) -> bool:
    """Validate task type against TASK_REGISTRY keys."""

    if not task_type:
        return False

    from app.services.agent_runner import TASK_REGISTRY

    return task_type in TASK_REGISTRY


def validate_task_input_data(task_type: str, input_data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate task input payload using agent runner validator."""

    from app.services.agent_runner import validate_task_inputs

    return validate_task_inputs(task_type, input_data)


def validate_url(url: str) -> bool:
    """Validate URL with http or https scheme and proper domain."""

    if not url:
        return False

    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False
    if "." not in parsed.netloc:
        return False
    return True


def validate_positive_integer(value: Any, field_name: str) -> tuple[bool, str]:
    """Validate that value is a positive integer."""

    try:
        numeric_value = int(value)
    except (ValueError, TypeError):
        return False, f"{field_name} must be a positive integer"

    if numeric_value <= 0:
        return False, f"{field_name} must be a positive integer"
    return True, ""


def validate_inr_amount(amount: Any) -> tuple[bool, str]:
    """Validate that amount is a non-negative number."""

    try:
        numeric_amount = float(amount)
    except (ValueError, TypeError):
        return False, "Amount must be a valid non-negative number"

    if numeric_amount < 0:
        return False, "Amount must be a valid non-negative number"
    return True, ""


def sanitize_task_inputs(input_data: dict[str, Any]) -> dict[str, Any]:
    """Sanitize task inputs by trimming strings, stripping null bytes, and truncating."""

    sanitized: dict[str, Any] = {}

    for key, value in input_data.items():
        if isinstance(value, str):
            cleaned = value.replace("\x00", "").strip()
            sanitized[key] = cleaned[:10000]
            continue

        if isinstance(value, list):
            cleaned_list: list[Any] = []
            for item in value:
                if isinstance(item, str):
                    cleaned_item = item.replace("\x00", "").strip()[:10000]
                    cleaned_list.append(cleaned_item)
                else:
                    cleaned_list.append(item)
            sanitized[key] = cleaned_list
            continue

        sanitized[key] = value

    return sanitized
