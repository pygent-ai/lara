"""Offload large inline images out of persisted conversation checkpoints.

Generated images reach the conversation as multi-megabyte inline payloads:
markdown ``data:image/...;base64,`` URIs inside assistant text and inline
media blocks in tool results. Storing them verbatim makes every session open
transfer and decode the full bytes again. The checkpoint boundary rewrites
those payloads into workspace files under ``.lara/uploads`` and keeps only
resource references, which the uploads endpoint and the pygent workspace
media resolver both resolve back to bytes.
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import Any

UPLOADS_SUBDIR = Path(".lara") / "uploads"

# Only rendered markdown images are rewritten; a bare data URI inside prose
# or a code fence never becomes an <img> node on the client.
_MARKDOWN_DATA_IMAGE = re.compile(
    r"(!\[[^\]]*\]\()data:image/([a-z0-9.+-]+);base64,([A-Za-z0-9+/=]+)(\))"
)

# Tool-result and message media blocks round-trip through strict field
# validation, so the replacement carries the full canonical source shape.
_RESOURCE_SOURCE_KEYS = ("url", "base64_data")

# Thumbnail-scale images stay inline: reloading them costs nothing and
# offloading would litter the uploads directory.
_INLINE_OFFLOAD_MIN_BYTES = 64 * 1024

_MIME_EXTENSIONS = {
    "png": ".png",
    "jpeg": ".jpg",
    "jpg": ".jpg",
    "gif": ".gif",
    "webp": ".webp",
    "bmp": ".bmp",
    "avif": ".avif",
    "svg+xml": ".svg",
}


def offload_inline_media(
    payload: dict[str, Any], *, workspace_root: str | Path
) -> dict[str, Any]:
    """Return `payload` with large inline images replaced by file references.

    The input is never mutated. Any storage failure returns the original
    payload, so history persistence degrades to inline bytes instead of
    failing the turn that produced the image.
    """

    if not str(workspace_root or "").strip():
        return payload
    try:
        return _offload(copy.deepcopy(payload), Path(workspace_root))
    except OSError:
        return payload


def _offload(node: Any, workspace_root: Path) -> Any:
    if isinstance(node, dict):
        source = node.get("source")
        if (
            node.get("type") == "media"
            and isinstance(source, dict)
            and source.get("kind") == "inline"
        ):
            replacement = _resource_source(
                source.get("base64_data"),
                node.get("mime_type"),
                workspace_root,
            )
            if replacement is not None:
                node["source"] = replacement
        for key, value in node.items():
            if key == "content" and isinstance(value, str):
                node[key] = _offload_content_data_uris(value, workspace_root)
            else:
                node[key] = _offload(value, workspace_root)
        return node
    if isinstance(node, list):
        return [_offload(item, workspace_root) for item in node]
    return node


def _offload_content_data_uris(content: str, workspace_root: Path) -> str:
    def replace(match: re.Match[str]) -> str:
        data = _decode_inline(match.group(3))
        if data is None or len(data) < _INLINE_OFFLOAD_MIN_BYTES:
            return match.group(0)
        relative = _store_media_bytes(
            data,
            workspace_root,
            _MIME_EXTENSIONS.get(match.group(2).lower(), ".png"),
        )
        return f"{match.group(1)}{relative}{match.group(4)}"

    return _MARKDOWN_DATA_IMAGE.sub(replace, content)


def _resource_source(
    encoded: Any, mime: Any, workspace_root: Path
) -> dict[str, Any] | None:
    data = _decode_inline(encoded)
    if data is None or len(data) < _INLINE_OFFLOAD_MIN_BYTES:
        return None
    relative = _store_media_bytes(data, workspace_root, _mime_extension(mime))
    return {
        "kind": "resource",
        "uri": relative,
        **{key: None for key in _RESOURCE_SOURCE_KEYS},
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def _decode_inline(value: Any) -> bytes | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None


def _mime_extension(mime: Any) -> str:
    subtype = str(mime or "").rpartition("/")[2].lower()
    return _MIME_EXTENSIONS.get(subtype, ".png")


def _store_media_bytes(
    data: bytes, workspace_root: Path, extension: str
) -> str:
    digest = hashlib.sha256(data).hexdigest()
    relative = (
        UPLOADS_SUBDIR / f"media-{digest[:16]}{extension}"
    ).as_posix()
    target = workspace_root / relative
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        # Content-addressed names make retries idempotent; the temp-file
        # rename keeps a crash mid-write from leaving a truncated image
        # behind under its final name.
        tmp = target.with_name(f"{target.name}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    return relative
