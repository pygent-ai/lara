"""Tests for the inbound Feishu bridge."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from lara_api.services.feishu_bridge import FeishuBridge


class _FakeEvent:
    def __init__(self, *, chat_id, message_type="text", text="你好", sender_type="user", message_id="mid-1"):
        message = SimpleNamespace(
            chat_id=chat_id,
            message_type=message_type,
            message_id=message_id,
            content=json.dumps({"text": text}, ensure_ascii=False),
        )
        sender = SimpleNamespace(sender_type=sender_type)
        self.event = SimpleNamespace(message=message, sender=sender)


def _bridge(tmp_path: Path, *, chats=("oc_chat",)) -> FeishuBridge:
    context = SimpleNamespace(
        config=SimpleNamespace(user_pygent_root=str(tmp_path)),
        chat_registry=SimpleNamespace(),
    )
    bridge = FeishuBridge(context, loop=asyncio.new_event_loop())
    from lara.runtime.realtime.feishu import FeishuBridgeConfig

    bridge._config = FeishuBridgeConfig(
        app_id="cli_x", app_secret="s3cret", chats=tuple(chats)
    )
    return bridge


def test_start_noops_when_bridge_config_missing(tmp_path: Path) -> None:
    context = SimpleNamespace(
        config=SimpleNamespace(user_pygent_root=str(tmp_path)),
        chat_registry=SimpleNamespace(),
    )
    bridge = FeishuBridge(context)

    assert bridge.start() is False
    assert bridge._thread is None


def test_event_filter_relays_only_user_text_from_bound_chats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = _bridge(tmp_path)
    relayed: list[str] = []
    monkeypatch.setattr(bridge, "_schedule_relay", lambda text: relayed.append(text))

    bridge._on_event(_FakeEvent(chat_id="oc_chat", text="你好"))
    assert relayed == ["你好"]

    bridge._on_event(_FakeEvent(chat_id="oc_stranger", text="别的群"))
    bridge._on_event(_FakeEvent(chat_id="oc_chat", message_type="image"))
    bridge._on_event(_FakeEvent(chat_id="oc_chat", sender_type="app", text="机器人自己"))
    bridge._on_event(_FakeEvent(chat_id="oc_chat", text="   "))
    assert relayed == ["你好"]

    # Feishu redelivers events; the same message id relays exactly once.
    bridge._on_event(_FakeEvent(chat_id="oc_chat", text="第二条", message_id="mid-2"))
    bridge._on_event(_FakeEvent(chat_id="oc_chat", text="第二条", message_id="mid-2"))
    assert relayed == ["你好", "第二条"]


class _FakeHost:
    """LocalExecutionHost stand-in: records deliveries and routes by mode."""

    def __init__(self, mode: str, *, reply: dict | None = None) -> None:
        self.mode = mode
        self.reply = reply
        self.deliveries: list[dict] = []

    async def deliver_user_message(self, **kwargs):
        self.deliveries.append(dict(kwargs))
        if self.mode == "interrupt":
            reply = self.reply or {}
            return SimpleNamespace(
                mode="interrupt",
                turn=None,
                outcome=SimpleNamespace(
                    speak_args=SimpleNamespace(
                        text=reply.get("text", ""),
                        attachments=(),
                        say_nothing=bool(reply.get("say_nothing", False)),
                    ),
                    text=None,
                ),
            )
        return SimpleNamespace(mode="turn", turn=_FakeTurn(), outcome=None)


class _FakeTurn:
    async def wait_ready(self) -> None:
        return None


class _FakeRun:
    def __init__(self, turn) -> None:
        self.turn = turn

    async def events(self, *, after=None, log_model_text_deltas=False):
        yield {"kind": "pygent.v1.chat.result"}
        return


def _service(tmp_path: Path, sessions: list) -> SimpleNamespace:
    return SimpleNamespace(
        list_chat_sessions=lambda: sessions,
        create_session=lambda **kwargs: SimpleNamespace(session_id="lara-says-1"),
        manager=SimpleNamespace(
            sessions_root=tmp_path / "sessions", config=SimpleNamespace()
        ),
    )


def _context(tmp_path: Path, host: _FakeHost) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(user_pygent_root=str(tmp_path)),
        execution_host=host,
    )


def test_relay_creates_the_says_session_and_delivers_a_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _FakeHost("turn")
    monkeypatch.setattr(
        "lara_api.services.feishu_bridge.session_service_for_scope",
        lambda context, scope_id: _service(tmp_path, []),
    )
    monkeypatch.setattr("lara_api.services.feishu_bridge.ActiveChatRun", _FakeRun)
    bridge = FeishuBridge(_context(tmp_path, host), loop=asyncio.new_event_loop())

    asyncio.run(bridge._relay("帮我看看代码"))

    assert len(host.deliveries) == 1
    assert host.deliveries[0]["session_id"] == "lara-says-1"
    assert host.deliveries[0]["message"] == "帮我看看代码"
    assert host.deliveries[0]["input_id"].startswith("feishu-")


def test_relay_sends_the_fork_reply_back_to_feishu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent_to_feishu: list[str] = []
    host = _FakeHost("interrupt", reply={"text": "马上给你结论"})
    sessions = [
        SimpleNamespace(
            session_id="lara-says-1",
            mode="pygent.v1-says",
            case_id="chat",
            last_case_run_status="running",
            last_case_run_id="run-1",
        )
    ]
    monkeypatch.setattr(
        "lara_api.services.feishu_bridge.session_service_for_scope",
        lambda context, scope_id: _service(tmp_path, sessions),
    )
    bridge = FeishuBridge(_context(tmp_path, host), loop=asyncio.new_event_loop())

    async def _send_to_feishu(text: str) -> None:
        sent_to_feishu.append(text)

    monkeypatch.setattr(bridge, "_send_to_feishu", _send_to_feishu)

    asyncio.run(bridge._relay("进展如何"))

    assert host.deliveries[0]["session_id"] == "lara-says-1"
    assert host.deliveries[0]["message"] == "进展如何"
    assert sent_to_feishu == ["马上给你结论"]


def test_relay_stays_silent_for_a_say_nothing_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent_to_feishu: list[str] = []
    host = _FakeHost("interrupt", reply={"text": "", "say_nothing": True})
    sessions = [SimpleNamespace(session_id="lara-says-1", mode="pygent.v1-says", case_id="chat")]
    monkeypatch.setattr(
        "lara_api.services.feishu_bridge.session_service_for_scope",
        lambda context, scope_id: _service(tmp_path, sessions),
    )
    bridge = FeishuBridge(_context(tmp_path, host), loop=asyncio.new_event_loop())
    monkeypatch.setattr(bridge, "_send_to_feishu", AsyncMock(side_effect=sent_to_feishu.append))

    asyncio.run(bridge._relay("还在吗"))

    assert sent_to_feishu == []
