from __future__ import annotations

import json
import time

import httpx
import pytest
from pygent import MediaSource
from pygent.llm import ModelConfig, OpenAICompatibleClient
from pygent.runtime.codec import message_from_dict, message_to_dict

from lara.runtime import model_configuration
from lara.runtime.attachments import (
    MAX_ATTACHMENTS,
    build_media_blocks,
    render_attachments_xml,
    resolve_attachments,
)
from lara.runtime.service import LaraRuntimeService
from lara.sessions import SessionManager
from tests.unit.test_model_configuration import native_runtime_config


@pytest.fixture()
def workspace(tmp_path):
    (tmp_path / "shots").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "shots" / "error.png").write_bytes(
        b"\x89PNG\r\n\x1a\nfake-image-bytes"
    )
    (tmp_path / "logs" / "app.log").write_text("line one\nline two\n", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "dump.bin").write_bytes(b"\x00\x01\x02\x03")
    return tmp_path


def test_classifies_media_text_and_reference(workspace):
    resolved = resolve_attachments(
        ["shots/error.png", "logs/app.log", "data/dump.bin"], workspace
    )
    assert [(item.delivery, item.media_type) for item in resolved] == [
        ("media", "image"),
        ("inline", None),
        ("reference", None),
    ]
    assert resolved[0].mime == "image/png"
    assert resolved[0].relative == "shots/error.png"
    assert resolved[1].mime.startswith("text/")
    assert resolved[1].size == (workspace / "logs" / "app.log").stat().st_size


def test_dedupes_and_preserves_order(workspace):
    resolved = resolve_attachments(
        ["logs/app.log", "logs/app.log", "shots/error.png", "logs/app.log"],
        workspace,
    )
    assert [item.relative for item in resolved] == [
        "logs/app.log",
        "shots/error.png",
    ]


def test_rejects_missing_and_escaping_paths(workspace):
    with pytest.raises(ValueError, match="not found"):
        resolve_attachments(["logs/missing.log"], workspace)
    with pytest.raises(ValueError, match="escapes the workspace"):
        resolve_attachments(["../outside.txt"], workspace)
    absolute = workspace.parent / "outside.txt"
    absolute.write_text("leak", encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="escapes the workspace"):
            resolve_attachments([str(absolute)], workspace)
    finally:
        absolute.unlink()


def test_rejects_too_many_attachments(workspace, tmp_path):
    paths = []
    for index in range(MAX_ATTACHMENTS + 1):
        target = workspace / f"file-{index}.log"
        target.write_text("x", encoding="utf-8")
        paths.append(target.name)
    with pytest.raises(ValueError, match="too many attachments"):
        resolve_attachments(paths, workspace)


def test_oversized_media_falls_back_to_reference(workspace):
    big = workspace / "shots" / "big.png"
    big.write_bytes(b"\x89PNG" + b"\x00" * 64)
    from lara.runtime import attachments as attachments_module

    original = attachments_module.MEDIA_MAX_BYTES
    try:
        attachments_module.MEDIA_MAX_BYTES = 8
        resolved = resolve_attachments(["shots/big.png"], workspace)
    finally:
        attachments_module.MEDIA_MAX_BYTES = original
    assert resolved[0].delivery == "reference"


def test_inline_text_truncation(workspace):
    big_log = workspace / "logs" / "big.log"
    big_log.write_text("x" * 100_000, encoding="utf-8")
    resolved = resolve_attachments(["logs/big.log"], workspace)
    assert resolved[0].delivery == "inline"
    xml = render_attachments_xml(resolved)
    assert 'truncated="true"' in xml
    assert "read tool for the full file" in xml


def test_xml_escapes_names_and_content(workspace):
    tricky = workspace / "logs" / "we&ird name.log"
    tricky.write_text('a < b & c > "d"', encoding="utf-8")
    resolved = resolve_attachments(["logs/we&ird name.log"], workspace)
    xml = render_attachments_xml(resolved)
    assert "we&amp;ird name" in xml
    assert "a &lt; b &amp; c &gt; &quot;d&quot;" not in xml  # body keeps quotes
    assert "a &lt; b &amp; c &gt; \"d\"" in xml


def test_media_blocks_roundtrip_through_history_codec(workspace):
    resolved = resolve_attachments(["shots/error.png"], workspace)
    blocks = build_media_blocks(resolved)
    assert len(blocks) == 1
    assert blocks[0].media_type == "image"
    assert blocks[0].source.kind == "resource"
    assert blocks[0].source.size_bytes == len(b"\x89PNG\r\n\x1a\nfake-image-bytes")
    restored = message_from_dict(
        message_to_dict(
            _user_message_with_media(blocks)  # type: ignore[arg-type]
        )
    )
    assert restored.media[0].source.kind == "resource"
    assert restored.media[0].source.uri == str(resolved[0].path)


def test_workspace_resolver_reads_inside_and_rejects_outside(workspace, tmp_path):
    from lara.runtime.model_configuration import workspace_media_resolver

    resolver = workspace_media_resolver(workspace)
    source = MediaSource.resource(str(workspace / "shots" / "error.png"))
    assert resolver(source).startswith(b"\x89PNG")
    outside = workspace.parent / "secret.png"
    outside.write_bytes(b"secret")
    try:
        with pytest.raises(ValueError, match="escapes the workspace"):
            resolver(MediaSource.resource(str(outside)))
    finally:
        outside.unlink()


def _user_message_with_media(blocks):
    from pygent import UserMessage

    return UserMessage(content="see attachments", media=blocks)


@pytest.mark.asyncio
async def test_start_turn_delivers_attachments_to_provider(
    workspace, monkeypatch,
):
    from PIL import Image

    real_png = workspace / "shots" / "real.png"
    Image.new("RGB", (4, 4), "red").save(real_png)
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        chunk = {
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "seen"},
                    "finish_reason": "stop",
                }
            ]
        }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as client:
        monkeypatch.setitem(
            model_configuration.CLIENT_FACTORIES,
            "openai_chat_completions",
            lambda **kwargs: OpenAICompatibleClient(
                base_url=kwargs["base_url"],
                api_key=kwargs["api_key"],
                client=client,
            ),
        )
        config = native_runtime_config(workspace, group=("main",))
        config.model_config_mapping["models"]["main"]["capabilities"][
            "modalities"
        ]["input"] = ["text", "image", "video"]
        config.model_config = ModelConfig.from_mapping(config.model_config_mapping)
        manager = SessionManager(config)
        session = manager.create(case_id="attach", mode="chat")
        run = manager.start_case_run(
            session.session_id, "attach", run_config=config
        )
        service = LaraRuntimeService(config)
        try:
            handle = await service.start_turn(
                manager=manager,
                message="check the screenshot and log",
                run_ref=run,
                turn_id="attach-turn",
                interactive_approvals=False,
                deadline=time.monotonic() + 60,
                attachments=["shots/real.png", "logs/app.log"],
            )
            answer, _ = await handle.result()
            assert answer.content == "seen"
        finally:
            manager.finish_case_run(run, "passed")
            await service.close()

    assert len(requests) == 1
    user_message = next(
        item
        for item in requests[0]["messages"]
        if item["role"] == "user"
    )
    assert isinstance(user_message["content"], list)
    text_part = next(
        item["text"] for item in user_message["content"] if item["type"] == "text"
    )
    assert '<attachments>' in text_part
    assert 'delivery="media"' in text_part
    assert "logs/app.log" in text_part
    image_part = next(
        item for item in user_message["content"] if item["type"] == "image_url"
    )
    delivered = image_part["image_url"]["url"]
    assert delivered.startswith("data:image/png;base64,")
    import base64

    assert (
        base64.b64decode(delivered.split(",", 1)[1]) == real_png.read_bytes()
    )
