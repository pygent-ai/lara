from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from lara.runtime.agent.media_offload import (
    UPLOADS_SUBDIR,
    offload_inline_media,
    offload_remote_media,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * (72 * 1024)
PNG_BASE64 = base64.b64encode(PNG_BYTES).decode("ascii")
PNG_SHA256 = hashlib.sha256(PNG_BYTES).hexdigest()


def stored_path(tmp_path: Path, extension: str = ".png") -> Path:
    return tmp_path / UPLOADS_SUBDIR / f"media-{PNG_SHA256[:16]}{extension}"


def test_offloads_markdown_data_uri_in_content(tmp_path: Path) -> None:
    payload = {
        "role": "assistant",
        "content": f"看这张图\n\n![image](data:image/png;base64,{PNG_BASE64})\n\n完毕",
    }
    result = offload_inline_media(payload, workspace_root=tmp_path)
    stored = (UPLOADS_SUBDIR / f"media-{PNG_SHA256[:16]}.png").as_posix()
    assert result["content"] == f"看这张图\n\n![image]({stored})\n\n完毕"
    assert stored_path(tmp_path).read_bytes() == PNG_BYTES


def test_offloads_inline_media_block_source(tmp_path: Path) -> None:
    payload = {
        "role": "tool",
        "results": [
            {
                "call_id": "call-1",
                "content": [
                    {"type": "text", "text": "read image"},
                    {
                        "type": "media",
                        "media_type": "image",
                        "mime_type": "image/jpeg",
                        "detail": None,
                        "source": {
                            "kind": "inline",
                            "uri": None,
                            "url": None,
                            "base64_data": PNG_BASE64,
                            "sha256": None,
                            "size_bytes": None,
                        },
                    },
                ],
            }
        ],
    }
    result = offload_inline_media(payload, workspace_root=tmp_path)
    source = result["results"][0]["content"][1]["source"]
    assert source == {
        "kind": "resource",
        "uri": (UPLOADS_SUBDIR / f"media-{PNG_SHA256[:16]}.jpg").as_posix(),
        "url": None,
        "base64_data": None,
        "sha256": PNG_SHA256,
        "size_bytes": len(PNG_BYTES),
    }
    assert stored_path(tmp_path, ".jpg").read_bytes() == PNG_BYTES


def test_small_inline_images_stay_inline(tmp_path: Path) -> None:
    tiny = base64.b64encode(b"tiny image bytes").decode("ascii")
    payload = {
        "role": "assistant",
        "media": [
            {
                "type": "media",
                "media_type": "image",
                "mime_type": "image/png",
                "source": {"kind": "inline", "base64_data": tiny},
            }
        ],
        "content": f"![icon](data:image/gif;base64,{tiny})",
    }
    result = offload_inline_media(payload, workspace_root=tmp_path)
    assert result["media"][0]["source"]["kind"] == "inline"
    assert "data:image/gif;base64," in result["content"]
    assert not (tmp_path / UPLOADS_SUBDIR).exists()


def test_malformed_base64_is_left_inline(tmp_path: Path) -> None:
    payload = {
        "role": "assistant",
        "content": "![broken](data:image/png;base64,not%%valid%%base64)",
    }
    result = offload_inline_media(payload, workspace_root=tmp_path)
    assert result["content"] == payload["content"]


def test_input_is_not_mutated(tmp_path: Path) -> None:
    payload = {
        "role": "assistant",
        "content": f"![image](data:image/png;base64,{PNG_BASE64})",
    }
    offload_inline_media(payload, workspace_root=tmp_path)
    assert payload["content"] == f"![image](data:image/png;base64,{PNG_BASE64})"


def test_storage_failure_keeps_original_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "role": "assistant",
        "content": f"![image](data:image/png;base64,{PNG_BASE64})",
    }

    def fail_write(self: Path, data: bytes) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", fail_write)
    result = offload_inline_media(payload, workspace_root=tmp_path)
    assert result is payload
    assert result["content"] == f"![image](data:image/png;base64,{PNG_BASE64})"


def test_same_image_reuses_one_file(tmp_path: Path) -> None:
    content = f"![image](data:image/png;base64,{PNG_BASE64})"
    first = offload_inline_media({"content": content}, workspace_root=tmp_path)
    second = offload_inline_media({"content": content}, workspace_root=tmp_path)
    assert first["content"] == second["content"]
    files = list((tmp_path / UPLOADS_SUBDIR).iterdir())
    assert [path.name for path in files] == [f"media-{PNG_SHA256[:16]}.png"]


def test_empty_workspace_root_is_a_noop() -> None:
    payload = {"content": f"![image](data:image/png;base64,{PNG_BASE64})"}
    assert offload_inline_media(payload, workspace_root="") is payload


REMOTE_URL = "https://r2.example.com/codex/6c9ea699.png"


def make_fetcher(data: bytes, calls: list[str]) -> object:
    async def fetch(url: str) -> bytes:
        calls.append(url)
        return data

    return fetch


async def test_offloads_remote_markdown_image(tmp_path: Path) -> None:
    calls: list[str] = []
    payload = {"content": f"看看这张\n\n![image]({REMOTE_URL})\n\n完毕"}
    result = await offload_remote_media(
        payload, workspace_root=tmp_path, fetch_url=make_fetcher(PNG_BYTES, calls)
    )
    stored = (UPLOADS_SUBDIR / f"media-{PNG_SHA256[:16]}.png").as_posix()
    assert result["content"] == f"看看这张\n\n![image]({stored})\n\n完毕"
    assert (tmp_path / stored).read_bytes() == PNG_BYTES
    assert calls == [REMOTE_URL]


async def test_offloads_remote_url_media_block(tmp_path: Path) -> None:
    calls: list[str] = []
    payload = {
        "role": "tool",
        "results": [
            {
                "call_id": "call-1",
                "content": [
                    {
                        "type": "media",
                        "media_type": "image",
                        "mime_type": "image/jpeg",
                        "source": {"kind": "url", "url": REMOTE_URL},
                    }
                ],
            }
        ],
    }
    result = await offload_remote_media(
        payload, workspace_root=tmp_path, fetch_url=make_fetcher(PNG_BYTES, calls)
    )
    source = result["results"][0]["content"][0]["source"]
    assert source == {
        "kind": "resource",
        "uri": (UPLOADS_SUBDIR / f"media-{PNG_SHA256[:16]}.jpg").as_posix(),
        "url": None,
        "base64_data": None,
        "sha256": PNG_SHA256,
        "size_bytes": len(PNG_BYTES),
    }
    assert (tmp_path / source["uri"]).read_bytes() == PNG_BYTES
    assert calls == [REMOTE_URL]


async def test_markdown_remote_image_sniffs_mime(tmp_path: Path) -> None:
    jpeg = b"\xff\xd8\xff" + b"1" * (72 * 1024)
    payload = {"content": f"![image]({REMOTE_URL})"}
    result = await offload_remote_media(
        payload, workspace_root=tmp_path, fetch_url=make_fetcher(jpeg, [])
    )
    sha = hashlib.sha256(jpeg).hexdigest()
    assert result["content"] == f"![image]({(UPLOADS_SUBDIR / f'media-{sha[:16]}.jpg').as_posix()})"


async def test_small_remote_images_stay_remote(tmp_path: Path) -> None:
    payload = {"content": f"![icon]({REMOTE_URL})"}
    result = await offload_remote_media(
        payload,
        workspace_root=tmp_path,
        fetch_url=make_fetcher(b"tiny image bytes", []),
    )
    assert result["content"] == f"![icon]({REMOTE_URL})"
    assert not (tmp_path / UPLOADS_SUBDIR).exists()


async def test_remote_fetch_failure_keeps_link(tmp_path: Path) -> None:
    async def failing(url: str) -> bytes:
        raise TimeoutError("host unreachable")

    payload = {"content": f"![image]({REMOTE_URL})"}
    result = await offload_remote_media(
        payload, workspace_root=tmp_path, fetch_url=failing
    )
    assert result["content"] == f"![image]({REMOTE_URL})"
    assert not (tmp_path / UPLOADS_SUBDIR).exists()


async def test_oversized_remote_image_stays_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("lara.runtime.agent.media_offload.MEDIA_MAX_BYTES", 1024)
    payload = {"content": f"![image]({REMOTE_URL})"}
    result = await offload_remote_media(
        payload, workspace_root=tmp_path, fetch_url=make_fetcher(PNG_BYTES, [])
    )
    assert result["content"] == f"![image]({REMOTE_URL})"
    assert not (tmp_path / UPLOADS_SUBDIR).exists()


async def test_no_fetcher_leaves_remote_urls(tmp_path: Path) -> None:
    payload = {"content": f"![image]({REMOTE_URL})"}
    result = await offload_remote_media(
        payload, workspace_root=tmp_path, fetch_url=None
    )
    assert result is payload
    assert result["content"] == f"![image]({REMOTE_URL})"
    assert not (tmp_path / UPLOADS_SUBDIR).exists()


async def test_non_http_url_is_not_fetched(tmp_path: Path) -> None:
    calls: list[str] = []
    payload = {
        "media": [
            {
                "type": "media",
                "mime_type": "image/png",
                "source": {"kind": "url", "url": "file:///etc/passwd"},
            }
        ]
    }
    result = await offload_remote_media(
        payload, workspace_root=tmp_path, fetch_url=make_fetcher(PNG_BYTES, calls)
    )
    assert result["media"][0]["source"]["kind"] == "url"
    assert calls == []
    assert not (tmp_path / UPLOADS_SUBDIR).exists()


async def test_same_remote_url_downloaded_once(tmp_path: Path) -> None:
    calls: list[str] = []
    payload = {
        "content": f"![one]({REMOTE_URL})\n\n![two]({REMOTE_URL})",
        "media": [
            {
                "type": "media",
                "mime_type": "image/png",
                "source": {"kind": "url", "url": REMOTE_URL},
            }
        ],
    }
    result = await offload_remote_media(
        payload, workspace_root=tmp_path, fetch_url=make_fetcher(PNG_BYTES, calls)
    )
    stored = (UPLOADS_SUBDIR / f"media-{PNG_SHA256[:16]}.png").as_posix()
    assert result["content"] == f"![one]({stored})\n\n![two]({stored})"
    assert result["media"][0]["source"]["uri"] == stored
    assert calls == [REMOTE_URL]
    assert [path.name for path in (tmp_path / UPLOADS_SUBDIR).iterdir()] == [
        f"media-{PNG_SHA256[:16]}.png"
    ]


async def test_remote_storage_failure_keeps_original_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {"content": f"![image]({REMOTE_URL})"}

    def fail_write(self: Path, data: bytes) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", fail_write)
    result = await offload_remote_media(
        payload, workspace_root=tmp_path, fetch_url=make_fetcher(PNG_BYTES, [])
    )
    assert result is payload
    assert result["content"] == f"![image]({REMOTE_URL})"


async def test_remote_offload_input_is_not_mutated(tmp_path: Path) -> None:
    payload = {"content": f"![image]({REMOTE_URL})"}
    await offload_remote_media(
        payload, workspace_root=tmp_path, fetch_url=make_fetcher(PNG_BYTES, [])
    )
    assert payload["content"] == f"![image]({REMOTE_URL})"
