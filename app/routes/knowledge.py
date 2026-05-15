"""Knowledge base blueprint routes."""

from __future__ import annotations

from datetime import datetime
import io
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from docx import Document
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user
from sqlalchemy import func, or_

from app.extensions import db
from app.models import AuditLog, KnowledgeBaseEntry, Plan, Project
from app.services.file_service import FileServiceError, file_service
from app.utils.decorators import login_required, org_required
from app.utils.response_helpers import error_response, success_response
from app.utils.sanitizer import sanitize_filename
from app.utils.validators import validate_file_extension, validate_url, validate_uuid

knowledge_bp = Blueprint("knowledge", __name__)

ALLOWED_KNOWLEDGE_EXTENSIONS = {"pdf", "docx", "txt", "csv", "md", "xlsx", "json"}


def _knowledge_plan_limit_mb(plan: Plan | None) -> int:
    if plan is None:
        return 100

    if isinstance(plan.features_json, dict):
        value = plan.features_json.get("knowledge_storage_limit_mb")
        if isinstance(value, int) and value > 0:
            return value

    defaults = {
        "free": 100,
        "starter": 512,
        "pro": 2048,
        "team": 10240,
        "enterprise": 51200,
    }
    return defaults.get(plan.slug, 100)


def _parse_date(value: str, is_end: bool = False) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
        if is_end:
            return parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
        return parsed
    except ValueError:
        return None


def _is_json_request() -> bool:
    requested_with = (request.headers.get("X-Requested-With") or "").strip().lower()
    return (
        request.is_json
        or request.accept_mimetypes.best == "application/json"
        or requested_with == "xmlhttprequest"
    )


def _safe_response_error(message: str, status: int = 400):
    if _is_json_request():
        return error_response(message, status)
    flash(message, "danger")
    return redirect(url_for("knowledge.knowledge_home"))


def _write_audit_log(action: str, entry: KnowledgeBaseEntry) -> None:
    user_agent = request.user_agent.string if request.user_agent else None
    db.session.add(
        AuditLog(
            org_id=g.org.id,
            user_id=current_user.id,
            action=action,
            resource_type="knowledge",
            resource_id=str(entry.id),
            ip_address=request.remote_addr,
            user_agent=user_agent[:500] if user_agent else None,
            extra_json={"source_type": entry.source_type, "title": entry.title[:120]},
        )
    )


def _resolve_project_id(project_id_raw: str | None) -> UUID | None:
    if not project_id_raw:
        return None
    project_id_str = str(project_id_raw).strip()
    if not project_id_str or not validate_uuid(project_id_str):
        return None

    project = Project.query.filter_by(
        id=UUID(project_id_str),
        org_id=g.org.id,
        is_deleted=False,
    ).first()
    return project.id if project else None


def _detect_mime_and_validate(extension: str, file_bytes: bytes) -> str | None:
    sample = file_bytes[:512]
    lower_ext = extension.lower().lstrip(".")

    if lower_ext == "pdf":
        return "application/pdf" if sample.startswith(b"%PDF") else None

    if lower_ext == "docx":
        if sample.startswith(b"PK\x03\x04"):
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return None

    if lower_ext == "xlsx":
        if sample.startswith(b"PK\x03\x04"):
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return None

    if lower_ext in {"txt", "md", "csv", "json"}:
        try:
            decoded = sample.decode("utf-8", errors="strict") if sample else ""
            if lower_ext == "json":
                stripped = decoded.strip()
                if stripped and not stripped.startswith("{") and not stripped.startswith("["):
                    return None
                return "application/json"
            if lower_ext == "md":
                return "text/markdown"
            if lower_ext == "csv":
                return "text/csv"
            return "text/plain"
        except UnicodeDecodeError:
            return None

    return None


def _extract_text_content(extension: str, file_bytes: bytes) -> str | None:
    lower_ext = extension.lower().lstrip(".")

    if lower_ext in {"txt", "md", "csv", "json"}:
        try:
            return file_bytes.decode("utf-8", errors="ignore")[:100000]
        except Exception:  # pylint: disable=broad-except
            return None

    if lower_ext == "pdf":
        try:
            import pdfplumber

            text_parts: list[str] = []
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text() or ""
                    if page_text:
                        text_parts.append(page_text)
            if not text_parts:
                return None
            return "\n\n".join(text_parts)[:100000]
        except Exception:  # pylint: disable=broad-except
            return None

    if lower_ext == "docx":
        try:
            document = Document(io.BytesIO(file_bytes))
            paragraphs = [paragraph.text for paragraph in document.paragraphs if paragraph.text]
            if not paragraphs:
                return None
            return "\n".join(paragraphs)[:100000]
        except Exception:  # pylint: disable=broad-except
            return None

    return None


def _load_entry_or_404(entry_id: str) -> KnowledgeBaseEntry:
    if not validate_uuid(entry_id):
        abort(404)

    entry = KnowledgeBaseEntry.query.filter_by(
        id=UUID(entry_id),
        org_id=g.org.id,
        is_deleted=False,
    ).first()
    if entry is None:
        abort(404)

    return entry


@knowledge_bp.get("/knowledge")
@login_required
@org_required
def knowledge_home():
    """Render knowledge base manager."""

    source_type = str(request.args.get("source_type") or "all").strip().lower()
    project_id = str(request.args.get("project_id") or "").strip()
    search = str(request.args.get("search") or "").strip()
    date_from = str(request.args.get("date_from") or "").strip()
    date_to = str(request.args.get("date_to") or "").strip()
    sort = str(request.args.get("sort") or "newest").strip().lower()
    page = max(request.args.get("page", default=1, type=int), 1)

    query = KnowledgeBaseEntry.query.filter_by(org_id=g.org.id, is_deleted=False)

    if source_type and source_type != "all":
        query = query.filter(KnowledgeBaseEntry.source_type == source_type)

    if project_id and validate_uuid(project_id):
        query = query.filter(KnowledgeBaseEntry.project_id == UUID(project_id))

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            or_(
                KnowledgeBaseEntry.title.ilike(pattern),
                KnowledgeBaseEntry.content_text.ilike(pattern),
            )
        )

    from_dt = _parse_date(date_from)
    to_dt = _parse_date(date_to, is_end=True)
    if from_dt:
        query = query.filter(KnowledgeBaseEntry.created_at >= from_dt)
    if to_dt:
        query = query.filter(KnowledgeBaseEntry.created_at <= to_dt)

    if sort == "oldest":
        query = query.order_by(KnowledgeBaseEntry.created_at.asc())
    elif sort == "largest":
        query = query.order_by(
            KnowledgeBaseEntry.file_size.desc().nullslast(),
            KnowledgeBaseEntry.created_at.desc(),
        )
    elif sort == "alphabetical":
        query = query.order_by(KnowledgeBaseEntry.title.asc())
    else:
        query = query.order_by(KnowledgeBaseEntry.created_at.desc())

    pagination = query.paginate(page=page, per_page=20, error_out=False)

    total_size_bytes = (
        db.session.query(func.coalesce(func.sum(KnowledgeBaseEntry.file_size), 0))
        .filter(
            KnowledgeBaseEntry.org_id == g.org.id,
            KnowledgeBaseEntry.is_deleted.is_(False),
            KnowledgeBaseEntry.file_size.isnot(None),
        )
        .scalar()
        or 0
    )

    projects = (
        Project.query.filter_by(org_id=g.org.id, is_deleted=False)
        .order_by(Project.name.asc())
        .all()
    )

    source_count_rows = (
        db.session.query(KnowledgeBaseEntry.source_type, func.count(KnowledgeBaseEntry.id))
        .filter(
            KnowledgeBaseEntry.org_id == g.org.id,
            KnowledgeBaseEntry.is_deleted.is_(False),
        )
        .group_by(KnowledgeBaseEntry.source_type)
        .all()
    )
    source_counts = {str(source): int(count) for source, count in source_count_rows}
    plan = db.session.get(Plan, g.org.plan_id) if g.org.plan_id else None

    return render_template(
        "app/knowledge.html",
        entries=pagination.items,
        pagination=pagination,
        total_entries=pagination.total,
        total_size_bytes=int(total_size_bytes),
        projects=projects,
        selected_source_type=source_type,
        selected_project_id=project_id,
        search=search,
        date_from=date_from,
        date_to=date_to,
        selected_sort=sort,
        source_counts=source_counts,
        plan_limit_mb=_knowledge_plan_limit_mb(plan),
    )


@knowledge_bp.post("/knowledge")
@login_required
@org_required
def create_knowledge_entry():
    """Create file, URL, or text knowledge entry."""

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        source_type = str(payload.get("source_type") or "text").strip().lower()
    else:
        payload = request.form.to_dict(flat=True)
        source_type = str(request.form.get("source_type") or payload.get("source_type") or "file").strip().lower()

    if source_type not in {"file", "url", "text"}:
        return _safe_response_error("Invalid source_type", 400)

    project_id = _resolve_project_id(payload.get("project_id"))

    entry = KnowledgeBaseEntry(
        org_id=g.org.id,
        project_id=project_id,
        created_by=current_user.id,
        title="",
        source_type=source_type,
    )

    if source_type == "file":
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return _safe_response_error("File is required", 400)

        if not validate_file_extension(upload.filename, ALLOWED_KNOWLEDGE_EXTENSIONS):
            return _safe_response_error("Unsupported file type", 400)

        safe_name = sanitize_filename(upload.filename)
        extension = safe_name.rsplit(".", 1)[1].lower()
        file_bytes = upload.read()
        file_size = len(file_bytes)
        max_bytes = int(current_app.config.get("MAX_CONTENT_LENGTH", 16 * 1024 * 1024))
        if file_size <= 0:
            return _safe_response_error("Uploaded file is empty", 400)
        if file_size > max_bytes:
            return _safe_response_error("File exceeds the 16MB limit", 400)

        detected_mime = _detect_mime_and_validate(extension, file_bytes)
        if detected_mime is None:
            return _safe_response_error("File signature does not match extension", 400)

        storage_path = file_service.save_knowledge_file(str(g.org.id), safe_name, file_bytes)
        title = str(payload.get("title") or "").strip() or safe_name

        entry.title = title[:500]
        entry.file_path = storage_path
        entry.file_name = safe_name
        entry.file_mime = detected_mime
        entry.file_size = file_size
        entry.content_text = _extract_text_content(extension, file_bytes)

    elif source_type == "url":
        source_url = str(payload.get("url") or "").strip()
        title = str(payload.get("title") or "").strip()
        if not source_url:
            return _safe_response_error("url is required", 400)
        if not validate_url(source_url):
            return _safe_response_error("Invalid URL", 400)
        if urlparse(source_url).scheme.lower() != "https":
            return _safe_response_error("Only HTTPS URLs are allowed", 400)
        if not title:
            return _safe_response_error("title is required", 400)

        entry.title = title[:500]
        entry.source_url = source_url[:500]
        entry.content_text = None

    else:
        title = str(payload.get("title") or "").strip()
        content = str(payload.get("content") or "").strip()
        if not title:
            return _safe_response_error("title is required", 400)
        if not content:
            return _safe_response_error("content is required", 400)
        if len(content) > 50000:
            return _safe_response_error("content cannot exceed 50000 characters", 400)

        entry.title = title[:500]
        entry.content_text = content

    db.session.add(entry)
    db.session.flush()
    _write_audit_log("knowledge.entry_added", entry)
    db.session.commit()

    if _is_json_request():
        return success_response(
            {
                "entry_id": str(entry.id),
                "title": entry.title,
                "source_type": entry.source_type,
                "file_size": int(entry.file_size or 0),
                "created_at": entry.created_at.isoformat() if entry.created_at else datetime.utcnow().isoformat(),
            }
        )

    flash("Knowledge entry added successfully", "success")
    return redirect(url_for("knowledge.knowledge_home"))


@knowledge_bp.get("/knowledge/<entry_id>")
@login_required
@org_required
def get_knowledge_entry(entry_id: str):
    """Return knowledge entry preview JSON or downloadable file."""

    entry = _load_entry_or_404(entry_id)

    preview_mode = request.args.get("preview") == "1" or _is_json_request()
    if entry.file_path and not preview_mode:
        try:
            absolute_path, mimetype = file_service.get_file_for_download(entry.file_path)
            return send_file(
                absolute_path,
                mimetype=mimetype,
                as_attachment=True,
                download_name=entry.file_name or "knowledge_file",
            )
        except FileServiceError:
            return error_response("File not found", 404)

    return success_response(
        {
            "id": str(entry.id),
            "title": entry.title,
            "source_type": entry.source_type,
            "source_url": entry.source_url,
            "content_text": entry.content_text,
            "file_name": entry.file_name,
            "file_mime": entry.file_mime,
            "file_size": int(entry.file_size or 0),
            "project_id": str(entry.project_id) if entry.project_id else None,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
            "download_url": url_for("knowledge.get_knowledge_entry", entry_id=str(entry.id)),
        }
    )


@knowledge_bp.delete("/knowledge/<entry_id>")
@login_required
@org_required
def delete_knowledge_entry(entry_id: str):
    """Soft delete a knowledge base entry."""

    entry = _load_entry_or_404(entry_id)

    if entry.file_path:
        file_service.delete_file(entry.file_path)

    entry.is_deleted = True
    entry.deleted_at = datetime.utcnow()
    _write_audit_log("knowledge.entry_deleted", entry)
    db.session.commit()

    return success_response({"deleted": True})


@knowledge_bp.put("/knowledge/<entry_id>")
@login_required
@org_required
def update_knowledge_entry(entry_id: str):
    """Update allowed fields for knowledge entries."""

    entry = _load_entry_or_404(entry_id)
    payload = request.get_json(silent=True) or {}

    if "content_text" in payload or "file_path" in payload:
        return error_response("content_text and file_path cannot be updated from this endpoint", 400)

    title = payload.get("title")
    project_id_raw = payload.get("project_id")

    if title is not None:
        title_text = str(title).strip()
        if not title_text:
            return error_response("title cannot be empty", 400)
        entry.title = title_text[:500]

    if project_id_raw is not None:
        if project_id_raw in ("", None):
            entry.project_id = None
        else:
            resolved = _resolve_project_id(str(project_id_raw))
            if resolved is None:
                return error_response("Invalid project_id", 400)
            entry.project_id = resolved

    db.session.commit()
    return success_response({"updated": True})
