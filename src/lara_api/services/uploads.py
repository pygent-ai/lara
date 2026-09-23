"""Clipboard/file uploads: raw bytes into the workspace for the attachment pipeline.

The chat attachment pipeline (lara.runtime.attachments) only accepts paths
inside the workspace, so pasted or picked files that are not workspace files
are stored under ``<workspace_root>/.lara/uploads`` first and referenced by
their returned relative path afterwards.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
UPLOADS_SUBDIR = Path(".lara") / "uploads"
_SNIFF_HEAD_BYTES = 64 * 1024
_SAFE_NAME_MAX = 120

_MAGIC_SIGNATURES: tuple[tuple[str, int, bytes], ...] = (
    ("image/png", 0, b"\x89PNG\r\n\x1a\n"),
    ("image/jpeg", 0, b"\xff\xd8\xff"),
    ("image/gif", 0, b"GIF87a"),
    ("image/gif", 0, b"GIF89a"),
    ("application/pdf", 0, b"%PDF-"),
    ("video/webm", 0, b"\x1a\x45\xdf\xa3"),
    ("audio/ogg", 0, b"OggS"),
    ("audio/flac", 0, b"fLaC"),
)
_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "application/pdf": ".pdf",
    "audio/wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
    "text/plain": ".txt",
}


@dataclass(frozen=True, slots=True)
class StoredUpload:
    """One uploaded file placed inside the workspace attachment area."""

    attachment_id: str
    name: str
    mime_type: str
    size_bytes: int
    relative: str


def sniff_mime(head: bytes) -> str:
    """Content-type from magic bytes; empty string when nothing matches."""

    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "video/mp4"
    if len(head) >= 12 and head[0:4] == b"RIFF":
        if head[8:12] == b"WEBP":
            return "image/webp"
        if head[8:12] == b"WAVE":
            return "audio/wav"
    for mime, offset, signature in _MAGIC_SIGNATURES:
        if head[offset : offset + len(signature)] == signature:
            return mime
    if head and b"\x00" not in head[:_SNIFF_HEAD_BYTES]:
        try:
            head[:_SNIFF_HEAD_BYTES].decode("utf-8")
        except UnicodeDecodeError:
            return ""
        return "text/plain"
    return ""


def sanitize_filename(name: str | None) -> str:
    """Strip directory parts and unsafe characters; keep the extension."""

    raw = Path((name or "").replace("\\", "/")).name
    cleaned = re.sub(r"[^\w.\- ()\u4e00-\u9fff]+", "_", raw).strip("._ ")
    suffix = Path(cleaned).suffix
    if len(cleaned) > _SAFE_NAME_MAX:
        cleaned = cleaned[: max(_SAFE_NAME_MAX - len(suffix), 1)] + suffix
    return cleaned.strip("._ ") or "attachment"


def store_upload(data: bytes, filename: str | None, workspace_root: str | Path) -> StoredUpload:
    """Persist one upload inside the workspace; returns its relative path."""

    if not data:
        raise ValueError("uploaded file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"uploaded file exceeds the {MAX_UPLOAD_BYTES}-byte limit"
        )
    root = Path(workspace_root).resolve()
    safe_name = sanitize_filename(filename)
    mime = sniff_mime(data[:_SNIFF_HEAD_BYTES]) or "application/octet-stream"
    if not Path(safe_name).suffix and mime in _MIME_EXTENSIONS:
        safe_name = f"{safe_name}{_MIME_EXTENSIONS[mime]}"
    attachment_id = uuid.uuid4().hex[:12]
    target = root / UPLOADS_SUBDIR / f"{attachment_id}-{safe_name}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return StoredUpload(
        attachment_id=attachment_id,
        name=safe_name,
        mime_type=mime,
        size_bytes=len(data),
        relative=target.relative_to(root).as_posix(),
    )


__all__ = [
    "MAX_UPLOAD_BYTES",
    "StoredUpload",
    "sniff_mime",
    "sanitize_filename",
    "store_upload",
]
