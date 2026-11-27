"""Plain-session steering through lara's execution host.

The SDK forks a reply only for the realtime (Lara说) session and queues every
other mid-turn message as the next turn. lara's host steers a plain session's
running turn instead: the message lands in the react inbox in immediate mode,
interrupts the in-flight step and becomes the next model input, carrying
attachments the way a turn does. A session without a running turn falls back
to the SDK path and queues a turn.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pygent import AIMessage
from pygent.llm import ModelExecution, ModelProviderResponse

from lara.orchestration import LocalExecutionHost
from lara.orchestration.session_execution import STEERING_MESSAGE_KIND
from lara.sessions import SessionManager
from tests.unit.test_model_configuration import native_runtime_config


class _BlockingInvoker:
    """Every model call blocks on ``release``; ``started[i]`` marks call i."""

    def __init__(self, calls: int) -> None:
        self.started = [asyncio.Event() for _ in range(calls)]
        self.release = asyncio.Event()
        self.requests: list[tuple] = []

    def validate_model(self, _model: object) -> None:
        return None

    async def aclose(self) -> None:
        return None

    def execute(self, *, message: object, context: object = None, **_kwargs):
        self.requests.append((message, context))
        index = len(self.requests)

        async def invoke(_emit: object) -> ModelProviderResponse:
            self.started[index - 1].set()
            await self.release.wait()
            return ModelProviderResponse(
                message=AIMessage(content=f"answer-{index}"), usage={}
            )

        return ModelExecution(invoke)


async def _hosted_plain(workspace: Path, invoker: _BlockingInvoker):
    """lara host + one plain chat session, invoker patched into the runtime."""

    config = native_runtime_config(workspace)
    assert config.resolved_agent is not None
    host = LocalExecutionHost()
    manager = SessionManager(config)
    session = manager.create("chat", mode="chat")
    lease = await host.acquire_runtime(config=config, manager=manager)
    service = lease.runtime.runtime_service
    service._model_invokers[config.resolved_agent.alias] = invoker
    for agent in service._agent_definitions.values():
        agent.llm = invoker
    await lease.release()
    return host, config, manager, session.session_id


def _steering_history(manager: SessionManager, session_id: str) -> list[dict]:
    return [
        item
        for item in manager.load(session_id).history
        if item.get("kind") == STEERING_MESSAGE_KIND
    ]


@pytest.mark.asyncio
async def test_plain_session_message_steers_the_running_turn_immediately(
    tmp_path: Path,
) -> None:
    invoker = _BlockingInvoker(calls=4)
    host, config, manager, session_id = await _hosted_plain(tmp_path, invoker)
    try:
        first = await host.deliver_user_message(
            config=config, manager=manager, session_id=session_id,
            message="original task", input_id="t1", interactive_approvals=False,
        )
        assert first.mode == "turn" and first.turn is not None
        await asyncio.wait_for(invoker.started[0].wait(), 10)

        second = await host.deliver_user_message(
            config=config, manager=manager, session_id=session_id,
            message="focus on tests", input_id="input-1",
        )
        assert second.mode == "steering"
        assert second.turn is first.turn
        assert second.input_id == "input-1"
        # Immediate mode: the blocked model call is interrupted and restarted
        # with the steering message before anything releases it.
        await asyncio.wait_for(invoker.started[1].wait(), 10)

        duplicate = await host.deliver_user_message(
            config=config, manager=manager, session_id=session_id,
            message="focus on tests", input_id="input-1",
        )
        assert duplicate.mode == "steering"
        third = await host.deliver_user_message(
            config=config, manager=manager, session_id=session_id,
            message="use Chinese", input_id="input-2",
        )
        assert third.mode == "steering"
        await asyncio.wait_for(invoker.started[2].wait(), 10)

        invoker.release.set()
        output, _ = await asyncio.wait_for(first.turn.result(), 10)
        assert output.content == "answer-3"

        assert len(invoker.requests) == 3
        message, context = invoker.requests[2]
        assert message.content == "use Chinese"
        assert any(item.content == "focus on tests" for item in context.messages)
        assert [item["content"] for item in _steering_history(manager, session_id)] == [
            "focus on tests",
            "use Chinese",
        ]

        # No running turn left: the same entry queues a turn as before.
        late = await host.deliver_user_message(
            config=config, manager=manager, session_id=session_id,
            message="late", input_id="input-3",
        )
        assert late.mode == "turn" and late.turn is not None
        assert late.turn is not first.turn
        output, _ = await asyncio.wait_for(late.turn.result(), 10)
        assert output.content == "answer-4"
        await host.aclose()

        async with LocalExecutionHost() as reopened:
            replay = await reopened.deliver_user_message(
                config=config, manager=manager, session_id=session_id,
                message="focus on tests", input_id="input-1",
            )
            assert replay.mode == "steering"
            assert replay.input_id == "input-1"
            assert replay.turn is not None
            assert replay.turn.execution_id == first.turn.execution_id
            assert [item["data"]["input_id"] for item in _steering_history(
                manager, session_id
            )] == ["input-1", "input-2"]
    finally:
        invoker.release.set()
        await host.aclose()


@pytest.mark.asyncio
async def test_plain_session_steering_carries_attachments(tmp_path: Path) -> None:
    invoker = _BlockingInvoker(calls=2)
    host, config, manager, session_id = await _hosted_plain(tmp_path, invoker)
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\nrest-of-image")
    try:
        first = await host.deliver_user_message(
            config=config, manager=manager, session_id=session_id,
            message="original task", input_id="t1", interactive_approvals=False,
        )
        assert first.turn is not None
        await asyncio.wait_for(invoker.started[0].wait(), 10)

        # A bad attachment fails before anything reaches the running turn.
        with pytest.raises(ValueError):
            await host.deliver_user_message(
                config=config, manager=manager, session_id=session_id,
                message="", input_id="bad", attachments=["missing.png"],
            )
        steered = await host.deliver_user_message(
            config=config, manager=manager, session_id=session_id,
            message="", input_id="input-1", attachments=["shot.png"],
        )
        assert steered.mode == "steering"
        await asyncio.wait_for(invoker.started[1].wait(), 10)
        invoker.release.set()
        await asyncio.wait_for(first.turn.result(), 10)

        message, context = invoker.requests[1]
        drained = [
            item
            for item in (message, *context.messages)
            if getattr(item, "kind", None) == STEERING_MESSAGE_KIND
        ]
        assert len(drained) == 1
        assert drained[0].content == ""
        assert list(drained[0].data["attachments"]) == ["shot.png"]
        assert len(drained[0].media) == 1
        assert drained[0].media[0].mime_type == "image/png"
        persisted = _steering_history(manager, session_id)
        assert persisted[0]["content"] == ""
        assert persisted[0]["data"]["attachments"] == ["shot.png"]
        assert persisted[0]["media"]
        assert "bad" not in {item["data"]["input_id"] for item in persisted}
    finally:
        invoker.release.set()
        await host.aclose()
