from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from lara_api.app import create_app
from lara_api.services.uploads import (
    MAX_UPLOAD_BYTES,
    delete_upload,
    resolve_upload_path,
    sanitize_filename,
    sniff_mime,
    store_upload,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"


def make_client(tmp_path):
    app = create_app(workspace_root=str(tmp_path))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_upload_stores_file_inside_workspace(tmp_path):
    async with make_client(tmp_path) as client:
        response = await client.post(
            "/uploads",
            files={"file": ("error.png", PNG_BYTES, "image/png")},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["mime_type"] == "image/png"
    assert body["name"] == "error.png"
    assert body["size_bytes"] == len(PNG_BYTES)
    assert body["path"].startswith(".lara/uploads/")
    stored = tmp_path / body["path"]
    assert stored.is_file()
    assert stored.read_bytes() == PNG_BYTES


@pytest.mark.asyncio
async def test_upload_resolves_project_scope(tmp_path):
    (tmp_path / "other").mkdir()
    async with make_client(tmp_path) as client:
        response = await client.post(
            "/uploads",
            files={"file": ("shot.png", PNG_BYTES, "image/png")},
            data={"scope_id": "not-a-scope"},
        )
    assert response.status_code == 400
    assert "Unknown session scope" in response.json()["detail"]


@pytest.mark.asyncio
async def test_upload_rejects_oversized_file(tmp_path):
    async with make_client(tmp_path) as client:
        response = await client.post(
            "/uploads",
            files={"file": ("big.bin", b"\x00" * (MAX_UPLOAD_BYTES + 1), None)},
        )
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_upload_rejects_empty_file(tmp_path):
    async with make_client(tmp_path) as client:
        response = await client.post(
            "/uploads",
            files={"file": ("empty.txt", b"", "text/plain")},
        )
    assert response.status_code == 400


def test_sniff_mime_detects_media_and_text():
    assert sniff_mime(PNG_BYTES) == "image/png"
    assert sniff_mime(b"GIF89a" + b"\x00" * 8) == "image/gif"
    assert sniff_mime(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8) == "video/mp4"
    assert sniff_mime(b"%PDF-1.7 rest") == "application/pdf"
    assert sniff_mime("中文笔记\n".encode("utf-8")) == "text/plain"
    assert sniff_mime(b"\x00\x01\x02\x03") == ""
    assert sniff_mime(b"") == ""


def test_sanitize_filename_strips_paths_and_unsafe_characters():
    assert sanitize_filename("..\\..\\windows\\system32\\evil.exe") == "evil.exe"
    assert sanitize_filename("report final v2.pdf") == "report final v2.pdf"
    assert sanitize_filename("笔记.md") == "笔记.md"
    assert sanitize_filename("a/b/c.txt") == "c.txt"
    assert sanitize_filename("") == "attachment"
    assert sanitize_filename("...") == "attachment"
    assert sanitize_filename("x" * 500 + ".png").endswith(".png")
    assert len(sanitize_filename("x" * 500 + ".png")) <= 124


def test_store_upload_appends_extension_for_extensionless_screenshots(tmp_path):
    stored = store_upload(PNG_BYTES, "image", tmp_path)
    assert stored.name.endswith(".png")
    assert (tmp_path / stored.relative).is_file()


def test_store_upload_rejects_oversized_bytes(tmp_path):
    with pytest.raises(ValueError, match="exceeds"):
        store_upload(b"\x00" * (MAX_UPLOAD_BYTES + 1), "big.bin", tmp_path)


def test_uploaded_path_flows_through_attachment_pipeline(tmp_path):
    from lara.runtime.attachments import resolve_attachments

    stored = store_upload(PNG_BYTES, "error.png", tmp_path)
    resolved = resolve_attachments([stored.relative], tmp_path)
    assert [(item.delivery, item.media_type, item.mime) for item in resolved] == [
        ("media", "image", "image/png")
    ]


def test_store_and_delete_upload_round_trip(tmp_path):
    stored = store_upload(PNG_BYTES, "error.png", tmp_path)
    target = delete_upload(tmp_path, stored.relative)
    assert target == tmp_path / stored.relative
    assert not target.exists()


def test_upload_paths_outside_uploads_directory_are_rejected(tmp_path):
    (tmp_path / "notes.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(ValueError, match="inside .lara/uploads"):
        resolve_upload_path(tmp_path, "notes.txt")
    with pytest.raises(ValueError, match="inside .lara/uploads"):
        resolve_upload_path(tmp_path, "../notes.txt")
    with pytest.raises(ValueError, match="inside .lara/uploads"):
        resolve_upload_path(tmp_path, ".lara/uploads/../../secret.txt")
    with pytest.raises(FileNotFoundError):
        delete_upload(tmp_path, ".lara/uploads/missing.png")
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "keep me"


@pytest.mark.asyncio
async def test_upload_content_serves_stored_bytes(tmp_path):
    async with make_client(tmp_path) as client:
        stored = store_upload(PNG_BYTES, "error.png", tmp_path)
        response = await client.get(
            "/uploads/content",
            params={"scope_id": None, "path": stored.relative},
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content == PNG_BYTES


@pytest.mark.asyncio
async def test_upload_content_rejects_missing_and_outside_paths(tmp_path):
    async with make_client(tmp_path) as client:
        missing = await client.get("/uploads/content", params={"path": ".lara/uploads/nope.png"})
        outside = await client.get("/uploads/content", params={"path": "notes.txt"})
        traversal = await client.get("/uploads/content", params={"path": ".lara/../notes.txt"})
    assert missing.status_code == 404
    assert outside.status_code == 400
    assert traversal.status_code == 400


@pytest.mark.asyncio
async def test_upload_delete_endpoint_removes_stored_file(tmp_path):
    async with make_client(tmp_path) as client:
        stored = store_upload(PNG_BYTES, "error.png", tmp_path)
        removed = await client.delete("/uploads", params={"path": stored.relative})
        assert removed.status_code == 204
        assert not (tmp_path / stored.relative).exists()

        repeat = await client.delete("/uploads", params={"path": stored.relative})
        assert repeat.status_code == 404


@pytest.mark.asyncio
async def test_upload_delete_endpoint_only_touches_uploads_directory(tmp_path):
    (tmp_path / "notes.txt").write_text("keep me", encoding="utf-8")
    async with make_client(tmp_path) as client:
        outside = await client.delete("/uploads", params={"path": "notes.txt"})
        traversal = await client.delete("/uploads", params={"path": ".lara/uploads/../..//notes.txt"})
        assert outside.status_code == 400
        assert traversal.status_code == 400
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "keep me"
