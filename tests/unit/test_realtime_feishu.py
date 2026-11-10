"""Tests for the Feishu speak delivery channel."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from lara.runtime.realtime.feishu import FeishuChannel, load_feishu_channels
from pygent.lara.runtime.realtime.speak import SILENT_ACKNOWLEDGEMENT, SPEAK_TOOL_SPEC
from lara.runtime.service import _SpeakExecutor


class _PostRecorder:
    """Records posts with their bearer token; returns scripted responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict, str | None]] = []

    def __call__(self, url, payload, *, token=None):
        self.calls.append((url, dict(payload), token))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _token_response(token="t-1", expires_in=7200):
    return {"code": 0, "access_token": token, "expires_in": expires_in, "token_type": "Bearer"}


def _message_response(code=0):
    return {"code": code, "msg": "ok" if code == 0 else "bad"}


def test_load_feishu_channels_requires_credentials_and_enabled_flag(tmp_path: Path) -> None:
    bridge = tmp_path / "feishu-bridge" / "sessions.json"
    bridge.parent.mkdir(parents=True)
    original = {key: os.environ.get(key) for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET")}
    try:
        os.environ["FEISHU_APP_ID"] = "cli_x"
        os.environ["FEISHU_APP_SECRET"] = "s3cret"
        assert load_feishu_channels(tmp_path) == ()

        bridge.write_text(
            json.dumps(
                {
                    "version": 1,
                    "enabled": False,
                    "sessions": {"oc_chat": {"session_id": "s1"}},
                }
            ),
            encoding="utf-8",
        )
        assert load_feishu_channels(tmp_path) == ()

        bridge.write_text(
            json.dumps(
                {
                    "version": 1,
                    "enabled": True,
                    "sessions": {
                        "oc_chat": {"session_id": "s1"},
                        "oc_other": {"session_id": "s2"},
                    },
                }
            ),
            encoding="utf-8",
        )
        channels = load_feishu_channels(tmp_path)
        assert [channel.chat_id for channel in channels] == ["oc_chat", "oc_other"]

        bridge.write_text("not json", encoding="utf-8")
        assert load_feishu_channels(tmp_path) == ()
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_load_feishu_channels_is_empty_without_credentials(tmp_path: Path) -> None:
    original = {key: os.environ.get(key) for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET")}
    try:
        for key in original:
            os.environ.pop(key, None)
        assert load_feishu_channels(tmp_path) == ()
    finally:
        for key, value in original.items():
            if value is not None:
                os.environ[key] = value


def test_deliver_posts_text_with_cached_tenant_token() -> None:
    recorder = _PostRecorder(
        [_token_response("t-abc"), _message_response(), _message_response()]
    )
    channel = FeishuChannel(
        app_id="cli_x", app_secret="s3cret", chat_id="oc_chat", post=recorder
    )

    assert channel.deliver("你好") is True

    token_url, token_payload, token_header = recorder.calls[0]
    assert "authen/v2/oauth/token" in token_url
    assert token_payload == {
        "grant_type": "client_credentials",
        "client_id": "cli_x",
        "client_secret": "s3cret",
    }
    assert token_header is None
    message_url, message_payload, message_token = recorder.calls[1]
    assert "receive_id_type=chat_id" in message_url
    assert message_payload["receive_id"] == "oc_chat"
    assert message_payload["msg_type"] == "text"
    assert json.loads(message_payload["content"]) == {"text": "你好"}
    assert message_token == "t-abc"

    # Second delivery reuses the cached token: only the message post happens.
    assert channel.deliver("第二条") is True
    assert len(recorder.calls) == 3
    assert "authen/v2/oauth/token" not in recorder.calls[2][0]


def test_deliver_refreshes_token_once_on_token_error() -> None:
    recorder = _PostRecorder(
        [
            _token_response("t-stale"),
            _message_response(code=99991663),
            _token_response("t-fresh"),
            _message_response(),
        ]
    )
    channel = FeishuChannel(
        app_id="cli_x", app_secret="s3cret", chat_id="oc_chat", post=recorder
    )

    assert channel.deliver("重试") is True
    token_posts = [call for call in recorder.calls if "token" in call[0]]
    assert [call[2] for call in token_posts] == [None, None]
    message_posts = [call for call in recorder.calls if "im/v1/messages" in call[0]]
    assert [call[2] for call in message_posts] == [
        "t-stale",
        "t-fresh",
    ]


def test_deliver_never_raises_on_transport_failure() -> None:
    recorder = _PostRecorder([RuntimeError("network down")])
    channel = FeishuChannel(
        app_id="cli_x", app_secret="s3cret", chat_id="oc_chat", post=recorder
    )

    assert channel.deliver("hello") is False


def test_deliver_surfaces_api_rejection_to_callers_of_deliver_internal() -> None:
    recorder = _PostRecorder([_token_response(), _message_response(code=230013)])
    channel = FeishuChannel(
        app_id="cli_x", app_secret="s3cret", chat_id="oc_chat", post=recorder
    )
    # deliver() swallows; the internal _deliver surfaces the API error.
    with pytest.raises(RuntimeError, match="230013"):
        channel._deliver("x")


def _speak_call(arguments):
    return SimpleNamespace(arguments=arguments)


def test_speak_executor_forwards_delivered_text_to_channels() -> None:
    delivered: list[str] = []

    class _Channel:
        def deliver(self, text: str) -> bool:
            delivered.append(text)
            return True

    executor = _SpeakExecutor(
        SimpleNamespace(config=SimpleNamespace(workspace_root=".")),
        channels=[_Channel()],
    )
    events: list[dict] = []

    async def emit(**kwargs):
        events.append(kwargs)

    async def _run():
        result = await executor.execute(
            SPEAK_TOOL_SPEC,
            _speak_call({"text": "给飞书也来一份"}),
            SimpleNamespace(emit=emit),
        )
        assert result == "delivered"
        await asyncio.gather(*executor._delivery_tasks)

    asyncio.run(_run())
    assert events[0]["kind"] == "lara.realtime.speak"
    assert events[0]["data"]["text"] == "给飞书也来一份"
    assert delivered == ["给飞书也来一份"]


def test_speak_executor_does_not_forward_say_nothing() -> None:
    forwarded: list[str] = []

    class _Channel:
        def deliver(self, text: str) -> bool:
            forwarded.append(text)
            return True

    executor = _SpeakExecutor(
        SimpleNamespace(config=SimpleNamespace(workspace_root=".")),
        channels=[_Channel()],
    )
    events: list[dict] = []

    async def emit(**kwargs):
        events.append(kwargs)

    async def _run():
        result = await executor.execute(
            SPEAK_TOOL_SPEC,
            _speak_call({"say_nothing": True}),
            SimpleNamespace(emit=emit),
        )
        assert result == SILENT_ACKNOWLEDGEMENT
        await asyncio.gather(*executor._delivery_tasks)

    asyncio.run(_run())
    assert events == []
    assert forwarded == []


def test_speak_executor_appends_attachment_paths_to_channel_text(tmp_path: Path) -> None:
    (tmp_path / "out.md").write_text("x", encoding="utf-8")
    forwarded: list[str] = []

    class _Channel:
        def deliver(self, text: str) -> bool:
            forwarded.append(text)
            return True

    executor = _SpeakExecutor(
        SimpleNamespace(config=SimpleNamespace(workspace_root=str(tmp_path))),
        channels=[_Channel()],
    )
    events: list[dict] = []

    async def emit(**kwargs):
        events.append(kwargs)

    async def _run():
        await executor.execute(
            SPEAK_TOOL_SPEC,
            _speak_call({"text": "结论在这", "attachments": ["out.md"]}),
            SimpleNamespace(emit=emit),
        )
        await asyncio.gather(*executor._delivery_tasks)

    asyncio.run(_run())
    assert events[0]["data"]["attachments"] == ["out.md"]
    assert forwarded == ["结论在这\nout.md"]
