from __future__ import annotations

import hashlib
import io
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .config import settings
from .errors import AppError


@dataclass(frozen=True)
class StoredImage:
    storage_key: str
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str


class StorageProvider:
    def save_image(self, content: bytes, filename: str, mime_type: str) -> StoredImage:
        raise NotImplementedError

    def open(self, storage_key: str) -> Path:
        raise NotImplementedError

    def delete(self, storage_key: str) -> None:
        raise NotImplementedError


class LocalStorageProvider(StorageProvider):
    allowed_mime = {"image/jpeg": ".jpg", "image/png": ".png"}

    def __init__(self, root: Path | None = None, max_bytes: int | None = None):
        self.root = (root or settings.storage_path).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes or settings.max_upload_size_mb * 1024 * 1024

    def save_image(self, content: bytes, filename: str, mime_type: str) -> StoredImage:
        if mime_type not in self.allowed_mime:
            raise AppError("UPLOAD_INVALID", "Only JPEG and PNG images are accepted", 422)
        if len(content) > self.max_bytes:
            raise AppError("UPLOAD_TOO_LARGE", "The image exceeds the upload size limit", 413)
        if not content:
            raise AppError("UPLOAD_INVALID", "The uploaded image is empty", 422)
        try:
            with Image.open(io.BytesIO(content)) as image:
                detected = image.format
                image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise AppError("UPLOAD_INVALID", "The image is corrupted or unsupported", 422) from exc
        if (mime_type == "image/jpeg" and detected != "JPEG") or (
            mime_type == "image/png" and detected != "PNG"
        ):
            raise AppError("UPLOAD_INVALID", "The MIME type does not match the image", 422)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(filename or "image").name)[:120] or "image"
        key = f"{uuid.uuid4().hex}{self.allowed_mime[mime_type]}"
        target = self._safe_path(key)
        target.write_bytes(content)
        return StoredImage(key, safe_name, mime_type, len(content), hashlib.sha256(content).hexdigest())

    def _safe_path(self, storage_key: str) -> Path:
        candidate = (self.root / storage_key).resolve()
        if candidate.parent != self.root or candidate.name != storage_key:
            raise AppError("UPLOAD_INVALID", "Invalid storage key", 422)
        return candidate

    def open(self, storage_key: str) -> Path:
        path = self._safe_path(storage_key)
        if not path.is_file():
            raise AppError("RESOURCE_NOT_FOUND", "Image not found", 404)
        return path

    def delete(self, storage_key: str) -> None:
        path = self._safe_path(storage_key)
        if path.exists():
            path.unlink()