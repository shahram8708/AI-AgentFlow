"""File management service."""

from __future__ import annotations

import mimetypes
import os
from datetime import datetime

from flask import current_app

from app.utils.sanitizer import sanitize_filename


class FileServiceError(Exception):
    """Raised when file operations fail or invalid paths are provided."""


class FileService:
    """Handles all file storage and retrieval for task outputs and uploads."""

    def __init__(self) -> None:
        self.upload_folder = os.environ.get("UPLOAD_FOLDER", "uploads")
        self.outputs_dir = os.path.join(self.upload_folder, "outputs")
        self.knowledge_dir = os.path.join(self.upload_folder, "knowledge")
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """Create upload directories if they do not exist."""

        os.makedirs(self.outputs_dir, exist_ok=True)
        os.makedirs(self.knowledge_dir, exist_ok=True)

    def save_output_file(self, task_id: str, content: str, extension: str = "txt") -> str:
        """Save output content to disk and return storage relative path."""

        filename = f"{task_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.{extension}"
        filename = sanitize_filename(filename)
        full_path = os.path.join(self.outputs_dir, filename)

        with open(full_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(content)

        return f"outputs/{filename}"

    def get_output_file_path(self, relative_path: str) -> str:
        """Return validated absolute path for a stored relative path."""

        full_path = os.path.join(self.upload_folder, relative_path)
        upload_root = os.path.realpath(self.upload_folder)
        resolved = os.path.realpath(full_path)

        if not resolved.startswith(upload_root):
            raise FileServiceError("Invalid file path")

        return resolved

    def read_output_file(self, relative_path: str) -> str:
        """Read UTF 8 content for a stored output file path."""

        full_path = self.get_output_file_path(relative_path)
        if not os.path.exists(full_path):
            raise FileServiceError("File not found")

        with open(full_path, "r", encoding="utf-8") as file_obj:
            return file_obj.read()

    def delete_file(self, relative_path: str) -> bool:
        """Delete a stored file if present."""

        try:
            full_path = self.get_output_file_path(relative_path)
            if os.path.exists(full_path):
                os.remove(full_path)
                return True

            current_app.logger.warning("File not found for deletion: %s", relative_path)
            return False
        except Exception as exc:  # pylint: disable=broad-except
            current_app.logger.warning("Failed deleting file %s: %s", relative_path, exc)
            return False

    def save_knowledge_file(self, org_id: str, filename: str, file_content: bytes) -> str:
        """Save binary knowledge file under organization scoped directory."""

        safe_filename = sanitize_filename(filename)
        full_path = os.path.join(self.knowledge_dir, str(org_id), safe_filename)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        with open(full_path, "wb") as file_obj:
            file_obj.write(file_content)

        return f"knowledge/{org_id}/{safe_filename}"

    def get_file_size(self, relative_path: str) -> int:
        """Return file size in bytes, or zero when file is unavailable."""

        try:
            full_path = self.get_output_file_path(relative_path)
            if not os.path.exists(full_path):
                return 0
            return int(os.path.getsize(full_path))
        except Exception:  # pylint: disable=broad-except
            return 0

    def get_file_for_download(self, relative_path: str) -> tuple[str, str]:
        """Return absolute file path and mime type for download response."""

        absolute_path = self.get_output_file_path(relative_path)
        if not os.path.exists(absolute_path):
            raise FileServiceError("File not found")

        guessed_mimetype, _ = mimetypes.guess_type(absolute_path)
        mimetype = guessed_mimetype or "application/octet-stream"
        return absolute_path, mimetype


file_service = FileService()
