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


def test_relay_sends_a_fresh_turn_when_idle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[object] = []

    class _FakeRun:
        def __init__(self) -> None:
            pass

        async def events(self, *, after=None, log_model_text_deltas=False):
            yield {"kind": "pygent.v1.chat.result"}
            return

    class _FakeRegistry:
        async def resolve(self, context, request):
            recorded.append(request)
            return _FakeRun()

    service = SimpleNamespace(
        list_chat_sessions=lambda: [],
        create_session=lambda **kwargs: SimpleNamespace(session_id="lara-says-1"),
        manager=SimpleNamespace(sessions_root=tmp_path / "sessions"),
    )
    monkeypatch.setattr(
        "lara_api.services.feishu_bridge.session_service_for_scope",
        lambda context, scope_id: service,
    )
    context = SimpleNamespace(
        config=SimpleNamespace(user_pygent_root=str(tmp_path)),
        chat_registry=_FakeRegistry(),
    )
    bridge = FeishuBridge(context, loop=asyncio.new_event_loop())

    asyncio.run(bridge._relay("帮我看看代码"))

    assert len(recorded) == 1
    request = recorded[0]
    assert request.session_id == "lara-says-1"
    assert request.scope_id == "pygent.v1-says"
    assert request.message == "帮我看看代码"


def test_relay_routes_running_session_to_fork_and_replies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent_to_feishu: list[str] = []
    steering_calls: list[dict] = []

    class _FakeRegistry:
        resolve = AsyncMock(side_effect=AssertionError("a running session must fork"))

        async def deliver_steering(self, execution_id, **kwargs):
            steering_calls.append({"execution_id": execution_id, **kwargs})
            return SimpleNamespace(
                reply={"via": "speak", "text": "马上给你结论", "say_nothing": False}
            )

    service = SimpleNamespace(
        list_chat_sessions=lambda: [
            SimpleNamespace(
                session_id="lara-says-1",
                mode="pygent.v1-says",
                case_id="chat",
                last_case_run_status="running",
                last_case_run_id="run-1",
            )
        ],
        manager=SimpleNamespace(sessions_root=tmp_path / "sessions"),
    )
    monkeypatch.setattr(
        "lara_api.services.feishu_bridge.session_service_for_scope",
        lambda context, scope_id: service,
    )
    run_metadata = tmp_path / "sessions" / "lara-says-1" / "cases" / "chat" / "runs" / "run-1"
    run_metadata.mkdir(parents=True)
    (run_metadata / "run_metadata.json").write_text(
        json.dumps({"runtime_execution_id": "exec-1"}), encoding="utf-8"
    )

    context = SimpleNamespace(
        config=SimpleNamespace(user_pygent_root=str(tmp_path)),
        chat_registry=_FakeRegistry(),
    )
    bridge = FeishuBridge(context, loop=asyncio.new_event_loop())

    async def _send_to_feishu(text: str) -> None:
        sent_to_feishu.append(text)

    monkeypatch.setattr(bridge, "_send_to_feishu", _send_to_feishu)

    asyncio.run(bridge._relay("进展如何"))

    assert steering_calls[0]["execution_id"] == "exec-1"
    assert steering_calls[0]["message"] == "进展如何"
    assert steering_calls[0]["session_id"] == "lara-says-1"
    assert sent_to_feishu == ["马上给你结论"]


def test_relay_falls_back_to_a_turn_when_the_execution_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved: list[object] = []

    class _FakeRun:
        async def events(self, *, after=None, log_model_text_deltas=False):
            if False:
                yield {}
            return

    class _FakeRegistry:
        async def deliver_steering(self, execution_id, **kwargs):
            raise LookupError("当前会话没有对应的运行中执行，请等待连接恢复后重试。")

        async def resolve(self, context, request):
            resolved.append(request)
            return _FakeRun()

    service = SimpleNamespace(
        list_chat_sessions=lambda: [
            SimpleNamespace(
                session_id="lara-says-1",
                mode="pygent.v1-says",
                case_id="chat",
                last_case_run_status="running",
                last_case_run_id="run-1",
            )
        ],
        manager=SimpleNamespace(sessions_root=tmp_path / "sessions"),
    )
    monkeypatch.setattr(
        "lara_api.services.feishu_bridge.session_service_for_scope",
        lambda context, scope_id: service,
    )
    run_metadata = tmp_path / "sessions" / "lara-says-1" / "cases" / "chat" / "runs" / "run-1"
    run_metadata.mkdir(parents=True)
    (run_metadata / "run_metadata.json").write_text("{}", encoding="utf-8")

    context = SimpleNamespace(
        config=SimpleNamespace(user_pygent_root=str(tmp_path)),
        chat_registry=_FakeRegistry(),
    )
    bridge = FeishuBridge(context, loop=asyncio.new_event_loop())

    asyncio.run(bridge._relay("还在吗"))

    assert len(resolved) == 1
    assert resolved[0].message == "还在吗"
