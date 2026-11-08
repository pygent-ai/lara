"""End-to-end realtime scenarios at the service level (D-RT series).

Each scenario drives a real LaraRuntimeService with a scripted invoker:

1. mid-turn interrupt — a running turn is fork-interrupted, the reply comes
   back through the managed fork execution, and the exchange envelope is
   claimed at the next tool-result boundary of the main turn;
2. consecutive interrupts — two interrupts produce two replies and two
   envelope deliveries;
3. proactive speak — the agent calls speak mid-turn, the executor emits
   ``lara.realtime.speak`` on the durable event stream;
4. finalizer — a pure-text ending queues exactly one bottom-line round
   through the session lane, whose reminder drives an explicit speak call.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from pygent import AIMessage, ToolCall
from pygent.llm import ModelExecution, ModelProviderResponse

from lara.orchestration.models import TurnCommand
from lara.orchestration.session_execution import SessionExecutionCoordinator
from lara.runtime.realtime.speak import SPEAK_TOOL_SPEC
from lara.runtime.realtime.turn_finalizer import TURN_FINALIZER_REMINDER
from lara.sessions import (
    LARA_SAYS_SESSION_MODE,
    SessionManager,
    realtime_enabled_for,
)
from tests.runtime_recovery_support import recovery_service


@pytest.fixture(autouse=True)
def _no_feishu_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Speak deliveries must never reach the real Feishu chat from tests."""

    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)


def _tool_call(call_id: str, name: str, arguments: dict) -> ToolCall:
    return ToolCall(call_id=call_id, name=name, arguments=arguments)


def _response(content: str = "", tool_calls: tuple = ()) -> ModelProviderResponse:
    return ModelProviderResponse(
        message=AIMessage(content=content, tool_calls=tool_calls), usage={}
    )


def _history_text(session) -> str:
    """The whole persisted conversation as text (content and tool payloads)."""

    import json as _json

    return _json.dumps(session.history, ensure_ascii=False, default=str)


def _realtime_service(workspace: Path):
    config, service = recovery_service(workspace)
    # Realtime interaction is session-gated now: the scenarios run in the
    # dedicated lara-says session, so only the fork deadline stays config.
    config.realtime_interaction.fork_deadline_seconds = 30
    return config, service


def _patch_invoker(config, service, invoker) -> None:
    assert config.resolved_agent is not None
    service._model_invokers[config.resolved_agent.alias] = invoker


class _ScriptedInvoker:
    """Routes model calls by markers in the incoming message content."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def validate_model(self, _model: object) -> None:
        return None

    async def aclose(self) -> None:
        return None

    def respond(self, content: str) -> ModelProviderResponse:  # pragma: no cover
        raise NotImplementedError

    def execute(self, *, message: object, context: object = None, **_kwargs):
        text = str(message.content)
        self.calls.append(text)

        async def invoke(_emit: object) -> ModelProviderResponse:
            return self.respond(text)

        return ModelExecution(invoke)


class _InterruptInvoker(_ScriptedInvoker):
    """Turn: bash(sleep) → final; fork/interrupts: speak; finalizer: speak."""

    def __init__(self, *, bash_seconds: int = 3) -> None:
        super().__init__()
        self.bash_seconds = bash_seconds

    def respond(self, content: str) -> ModelProviderResponse:
        if "进度如何" in content:
            return _response(
                tool_calls=(_tool_call("fork-1", "speak", {"text": "在做了，快好了"}),)
            )
        if TURN_FINALIZER_REMINDER in content:
            return _response(
                tool_calls=(_tool_call("fin-1", "speak", {"text": "草稿说完了"}),)
            )
        if "user-message" in content:
            # A fresh user turn: start the slow bash probe.
            return _response(
                tool_calls=(
                    _tool_call("t1", "bash", {"command": f"sleep {self.bash_seconds}"}),
                )
            )
        # Tool-result continuation rounds finish the turn.
        return _response(content="final answer")


async def _wait_for(condition, timeout: float = 15.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_mid_turn_interrupt_forks_reply_and_delivers_envelope() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, service = _realtime_service(Path(tmp))
        try:
            manager = SessionManager(config)
            session = manager.create(case_id="chat", mode=LARA_SAYS_SESSION_MODE)
            invoker = _InterruptInvoker()
            _patch_invoker(config, service, invoker)
            loop = asyncio.get_running_loop()

            run_ref = manager.start_case_run(session.session_id, "chat", run_config=config)
            handle = await service.start_turn(
                manager=manager,
                message="first",
                run_ref=run_ref,
                turn_id="turn-1",
                interactive_approvals=False,
                deadline=loop.time() + 60,
            )
            # The bash tool is running: interrupt mid-turn.
            await _wait_for(lambda: len(invoker.calls) >= 1)
            await asyncio.sleep(0.3)
            outcome = await service.send_interrupt(
                handle.execution_id,
                manager=manager,
                run_ref=run_ref,
                session_id=session.session_id,
                input_id="input-1",
                message="进度如何",
            )
            assert outcome.via == "speak"
            assert outcome.speak_args is not None
            assert outcome.speak_args.text == "在做了，快好了"

            output, _ = await handle.result()
            assert output.content == "final answer"

            restored = manager.load(session.session_id)
            tool_contents = [
                str(message.get("content"))
                for message in restored.history
                if message.get("role") == "tool"
            ]
            joined = "\n".join(tool_contents)
            assert "user-asked" in joined
            assert "进度如何" in joined
            assert "在做了，快好了" in joined
        finally:
            await service.close()


@pytest.mark.asyncio
async def test_consecutive_interrupts_produce_two_replies_and_envelopes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, service = _realtime_service(Path(tmp))
        try:
            manager = SessionManager(config)
            session = manager.create(case_id="chat", mode=LARA_SAYS_SESSION_MODE)
            invoker = _InterruptInvoker()
            _patch_invoker(config, service, invoker)
            loop = asyncio.get_running_loop()

            run_ref = manager.start_case_run(session.session_id, "chat", run_config=config)
            handle = await service.start_turn(
                manager=manager,
                message="first",
                run_ref=run_ref,
                turn_id="turn-1",
                interactive_approvals=False,
                deadline=loop.time() + 60,
            )
            await _wait_for(lambda: len(invoker.calls) >= 1)
            await asyncio.sleep(0.3)
            first = await service.send_interrupt(
                handle.execution_id,
                manager=manager,
                run_ref=run_ref,
                session_id=session.session_id,
                input_id="input-1",
                message="进度如何",
            )
            second = await service.send_interrupt(
                handle.execution_id,
                manager=manager,
                run_ref=run_ref,
                session_id=session.session_id,
                input_id="input-2",
                message="进度如何",
            )
            assert first.via == "speak" and second.via == "speak"
            await handle.result()

            restored = manager.load(session.session_id)
            joined = "\n".join(
                str(message.get("content"))
                for message in restored.history
                if message.get("role") == "tool"
            )
            assert joined.count("user-asked") >= 2
        finally:
            await service.close()


class _SpeakInvoker(_ScriptedInvoker):
    """Turn: speak(text) → final."""

    def respond(self, content: str) -> ModelProviderResponse:
        if "lara.realtime.speak" in content:
            return _response(content="final answer")
        if self.calls and len(self.calls) == 1:
            return _response(
                tool_calls=(
                    _tool_call("s1", "speak", {"text": "进展：一切顺利"}),
                )
            )
        return _response(content="final answer")


@pytest.mark.asyncio
async def test_agent_speak_delivers_stream_event_and_tool_result() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, service = _realtime_service(Path(tmp))
        try:
            manager = SessionManager(config)
            session = manager.create(case_id="chat", mode=LARA_SAYS_SESSION_MODE)
            invoker = _SpeakInvoker()
            _patch_invoker(config, service, invoker)
            loop = asyncio.get_running_loop()

            run_ref = manager.start_case_run(session.session_id, "chat", run_config=config)
            handle = await service.start_turn(
                manager=manager,
                message="first",
                run_ref=run_ref,
                turn_id="turn-1",
                interactive_approvals=False,
                deadline=loop.time() + 60,
            )
            output, _ = await handle.result()
            assert output.content == "final answer"

            events = await service.history.events_tail(
                execution_id=handle.execution_id, limit=256
            )
            assert any(
                "lara.realtime.speak" in str(event) for event in events
            ), "the speak bubble event must be on the durable stream"

            restored = manager.load(session.session_id)
            assert "delivered" in _history_text(restored)
        finally:
            await service.close()


class _PureTextInvoker(_ScriptedInvoker):
    """Turn 1: pure text (triggers the finalizer); finalizer: speak → text."""

    def __init__(self) -> None:
        super().__init__()
        self.finalizer_seen = False

    def respond(self, content: str) -> ModelProviderResponse:
        if TURN_FINALIZER_REMINDER in content:
            self.finalizer_seen = True
            return _response(
                tool_calls=(_tool_call("fin-1", "speak", {"text": "草稿说完了"}),)
            )
        if "lara.realtime.speak" in content or self.finalizer_seen:
            return _response(content="done talking")
        return _response(content="thinking in plain text")


@pytest.mark.asyncio
async def test_pure_text_ending_queues_exactly_one_finalizer_round() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, service = _realtime_service(Path(tmp))
        manager = SessionManager(config)
        session = manager.create(case_id="chat", mode=LARA_SAYS_SESSION_MODE)
        invoker = _PureTextInvoker()
        _patch_invoker(config, service, invoker)

        lease = SimpleNamespace(
            runtime=SimpleNamespace(manager=manager, runtime_service=service),
            release=asyncio.sleep(0),
        )
        coordinator = SessionExecutionCoordinator()
        try:
            turn = await coordinator.submit_turn(
                lease=lease,
                command=TurnCommand(
                    session_id=session.session_id,
                    message="first",
                    case_id="chat",
                    turn_id="turn-1",
                    interactive_approvals=False,
                    message_kind="lara.chat.turn",
                ),
            )
            await turn.finalized.wait()
        # The lane queues the finalizer round right after the pure-text end.
            deadline = asyncio.get_running_loop().time() + 30
            while not invoker.finalizer_seen:
                if asyncio.get_running_loop().time() > deadline:
                    raise AssertionError("finalizer round never ran")
                await asyncio.sleep(0.1)
        # Wait for the finalizer turn itself to settle.
            deadline = asyncio.get_running_loop().time() + 30
            while len(invoker.calls) < 3:
                if asyncio.get_running_loop().time() > deadline:
                    break
                await asyncio.sleep(0.1)
        finally:
            await service.close()
        await coordinator.close()

        # turn-1 model call, finalizer model call, post-speak model call.
        assert invoker.finalizer_seen
        assert any(TURN_FINALIZER_REMINDER in call for call in invoker.calls[1:])
        # Exactly one finalizer round: no further model call after the
        # finalizer turn's silent ending.
        assert len(invoker.calls) == 3




def test_realtime_flag_is_exclusive_to_the_lara_says_session() -> None:
    """Regular sessions never get realtime interaction, config notwithstanding."""

    assert realtime_enabled_for(
        SimpleNamespace(metadata={"mode": LARA_SAYS_SESSION_MODE})
    ) is True
    assert realtime_enabled_for(SimpleNamespace(metadata={"mode": "chat"})) is False
    assert realtime_enabled_for(SimpleNamespace(metadata={})) is False
