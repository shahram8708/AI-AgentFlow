"""Input sanitization helpers."""

from __future__ import annotations

import re

from markupsafe import Markup
from werkzeug.utils import secure_filename

SAFE_TAGS = {
    "b",
    "strong",
    "i",
    "em",
    "u",
    "br",
    "p",
    "ul",
    "ol",
    "li",
    "code",
    "pre",
    "span",
}


def sanitize_html(text: str) -> str:
    """Sanitize HTML by stripping all tags except a safe allowlist."""

    if not text:
        return ""

    tag_re = re.compile(r"</?([a-zA-Z0-9]+)(?:\s[^>]*)?>")

    def _clean_tag(match: re.Match[str]) -> str:
        raw = match.group(0)
        name = match.group(1).lower()
        if name not in SAFE_TAGS:
            return ""
        closing = "/" if raw.startswith("</") else ""
        return f"<{closing}{name}>"

    cleaned = tag_re.sub(_clean_tag, text)
    return str(Markup(cleaned))


def sanitize_filename(filename: str) -> str:
    """Sanitize filename for safe filesystem storage."""

    return secure_filename(filename)


def strip_sql_injection(value: str) -> str:
    """Apply basic SQL injection hardening for untrusted input."""

    if value is None:
        return ""

    cleaned = value
    cleaned = cleaned.replace("--", "")
    cleaned = cleaned.replace(";", "")
    cleaned = cleaned.replace("/*", "")
    cleaned = cleaned.replace("*/", "")
    cleaned = cleaned.replace("'", "''")
    return cleaned.strip()
