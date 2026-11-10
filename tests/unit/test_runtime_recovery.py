from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from pygent import AIMessage, ToolCall, ToolMessage
from pygent.llm import ModelCallError, ModelErrorKind, ModelExecution, ModelProviderResponse
from pygent.runtime.codec import context_from_dict

from pygent.lara.runtime.context import LARA_CONTEXT_CODECS, LaraContext
from lara.sessions import SessionManager
from tests.runtime_recovery_support import recovery_service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("boundary", "force_compression"),
    [
        ("user", False),
        ("assistant", False),
        ("tool", False),
        ("assistant", True),
    ],
)
async def test_pygent_recovers_lara_turn_from_durable_message_boundary(
    boundary: str,
    force_compression: bool,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        execution_path = workspace / "execution-id.txt"
        reached_path = workspace / "reached.txt"
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.runtime_recovery_support",
                str(workspace),
                boundary,
                str(execution_path),
                str(reached_path),
                "on" if force_compression else "off",
            ],
            cwd=Path(__file__).parents[2],
            timeout=30,
            check=False,
        )
        assert process.returncode == 91
        assert reached_path.read_text(encoding="utf-8") == boundary
        execution_id = execution_path.read_text(encoding="utf-8")

        config, service = recovery_service(
            workspace,
            force_compression=force_compression,
        )
        manager = SessionManager(config)
        run_ref = await service.recovery_case_run(execution_id)
        interrupted = manager.load(run_ref.session_id)
        assert interrupted.history[-1]["role"] == boundary
        output_path = workspace / "recovery-output.txt"
        interrupted_mtime = output_path.stat().st_mtime_ns if output_path.exists() else None

        try:
            handle = await service.recover_turn(
                execution_id,
                deadline=asyncio.get_running_loop().time() + 30,
            )
            output, _ = await handle.result()
        finally:
            await service.close()

        restored = manager.load(run_ref.session_id)
        assert output.content == "recovered final answer"
        assert output_path.read_text(encoding="utf-8") == "durable tool output"
        if boundary == "tool":
            assert output_path.stat().st_mtime_ns == interrupted_mtime
        expected_roles = [
            "user",
            "assistant",
            "tool",
            "assistant",
        ]
        if force_compression:
            expected_roles = ["user", "assistant", *expected_roles]
        assert [message["role"] for message in restored.history] == expected_roles
        assert restored.history[-1]["tool_calls"] == []
        if force_compression:
            agent_context = context_from_dict(
                restored.metadata["agent_context"],
                registry=LARA_CONTEXT_CODECS,
            )
            assert isinstance(agent_context, LaraContext)
            assert agent_context.compression_count >= 1
            assert agent_context.projection_revision >= 1
            session_dir = Path(restored.session_dir)
            assert not (session_dir / "model_context.json").exists()
            assert not (session_dir / "transcript.jsonl").exists()
            assert not (session_dir / "compactions.jsonl").exists()
            assert "pygent_agent_state" not in restored.metadata


class _FailSecondRoundInvoker:
    """Scripted model: turn 1 passes, turn 2 dies after one tool round."""

    def __init__(self) -> None:
        self.contexts: list[object] = []

    def validate_model(self, _model: object) -> None:
        return None

    async def aclose(self) -> None:
        return None

    def execute(self, *, message: object, context: object = None, **_kwargs: object):
        self.contexts.append(context)

        async def invoke(_emit: object) -> ModelProviderResponse:
            if isinstance(message, ToolMessage):
                raise ModelCallError(
                    "injected mid-round failure",
                    kind=ModelErrorKind.AUTHENTICATION,
                )
            if "<user-message>second</user-message>" in str(message.content):
                return ModelProviderResponse(
                    message=AIMessage(
                        content="failing round",
                        tool_calls=(
                            ToolCall(
                                call_id="probe-1",
                                name="bash",
                                arguments={"command": "echo probe"},
                            ),
                        ),
                    ),
                    usage={},
                )
            return ModelProviderResponse(
                message=AIMessage(content="final answer"), usage={}
            )

        return ModelExecution(invoke)


@pytest.mark.asyncio
async def test_next_turn_replays_the_conversation_of_a_failed_mid_round_turn() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        config, service = recovery_service(workspace)
        manager = SessionManager(config)
        session_ref = manager.create(case_id="replay", mode="chat")
        invoker = _FailSecondRoundInvoker()
        assert config.resolved_agent is not None
        service._model_invokers[config.resolved_agent.alias] = invoker
        for agent in service._agent_definitions.values():
            agent.llm = invoker

        async def run_turn(turn_id: str, message: str):
            run_ref = manager.start_case_run(
                session_ref.session_id, "replay", run_config=config
            )
            handle = await service.start_turn(
                manager=manager,
                message=message,
                run_ref=run_ref,
                turn_id=turn_id,
                interactive_approvals=False,
                deadline=asyncio.get_running_loop().time() + 60,
            )
            return await handle.result()

        try:
            await run_turn("turn-1", "first")
            with pytest.raises(Exception):
                await run_turn("turn-2", "second")

            # The failed round is durable, while the checkpoint predates it.
            stale = manager.load(session_ref.session_id)
            assert [item["role"] for item in stale.history] == [
                "user",
                "assistant",
                "user",
                "assistant",
                "tool",
            ]
            stale_context = context_from_dict(
                stale.metadata["agent_context"], registry=LARA_CONTEXT_CODECS
            )
            assert isinstance(stale_context, LaraContext)
            assert len(stale_context.messages) == 2
            assert stale.metadata["agent_context_history_count"] == 2

            output, _ = await run_turn("turn-3", "third")
            assert output.content == "final answer"

            # The model of turn 3 sees the failed round after the checkpoint.
            third_context = invoker.contexts[-1]
            assert [message.role for message in third_context.messages] == [
                "user",
                "assistant",
                "user",
                "assistant",
                "tool",
            ]
            assert "<user-message>second</user-message>" in (
                third_context.messages[2].content
            )

            healed = manager.load(session_ref.session_id)
            agent_context = context_from_dict(
                healed.metadata["agent_context"], registry=LARA_CONTEXT_CODECS
            )
            assert isinstance(agent_context, LaraContext)
            assert [message.role for message in agent_context.messages] == [
                "user",
                "assistant",
                "user",
                "assistant",
                "tool",
                "user",
                "assistant",
            ]
            assert healed.metadata["agent_context_history_count"] == len(
                healed.history
            )
        finally:
            await service.close()


class _CancelMidRoundInvoker:
    """Turn 1 answers, turn 2 starts a slow tool and waits for the cancel."""

    def __init__(self) -> None:
        self.contexts: list[object] = []

    def validate_model(self, _model: object) -> None:
        return None

    async def aclose(self) -> None:
        return None

    def execute(self, *, message: object, context: object = None, **_kwargs: object):
        self.contexts.append(context)

        async def invoke(_emit: object) -> ModelProviderResponse:
            if "<user-message>second</user-message>" in str(message.content):
                return ModelProviderResponse(
                    message=AIMessage(
                        content="starting slow tool",
                        tool_calls=(
                            ToolCall(
                                call_id="probe-cancel",
                                name="bash",
                                arguments={"command": "sleep 8"},
                            ),
                        ),
                    ),
                    usage={},
                )
            return ModelProviderResponse(
                message=AIMessage(content="final answer"), usage={}
            )

        return ModelExecution(invoke)


@pytest.mark.asyncio
async def test_next_turn_replays_the_conversation_of_a_cancelled_mid_round_turn() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        config, service = recovery_service(workspace)
        manager = SessionManager(config)
        session_ref = manager.create(case_id="replay", mode="chat")
        invoker = _CancelMidRoundInvoker()
        assert config.resolved_agent is not None
        service._model_invokers[config.resolved_agent.alias] = invoker
        for agent in service._agent_definitions.values():
            agent.llm = invoker
        loop = asyncio.get_running_loop()

        async def run_turn(turn_id: str, message: str):
            run_ref = manager.start_case_run(
                session_ref.session_id, "replay", run_config=config
            )
            return await service.start_turn(
                manager=manager,
                message=message,
                run_ref=run_ref,
                turn_id=turn_id,
                interactive_approvals=False,
                deadline=loop.time() + 60,
            )

        try:
            handle = await run_turn("turn-1", "first")
            await handle.result()

            handle = await run_turn("turn-2", "second")
            # Cancel while the slow tool is in flight: the assistant tool call
            # is durable, its result never will be.
            deadline = loop.time() + 15
            while loop.time() < deadline:
                history = manager.load(session_ref.session_id).history
                if history[-1]["role"] == "assistant" and history[-1].get(
                    "tool_calls"
                ):
                    break
                await asyncio.sleep(0.05)
            await handle.cancel()
            with pytest.raises(BaseException):
                await handle.result()

            stale = manager.load(session_ref.session_id)
            assert stale.metadata["last_case_run_status"] == "skipped"
            assert [item["role"] for item in stale.history] == [
                "user",
                "assistant",
                "user",
                "assistant",
            ]
            stale_context = context_from_dict(
                stale.metadata["agent_context"], registry=LARA_CONTEXT_CODECS
            )
            assert isinstance(stale_context, LaraContext)
            assert len(stale_context.messages) == 2
            assert stale.metadata["agent_context_history_count"] == 2

            handle = await run_turn("turn-3", "third")
            output, _ = await handle.result()
            assert output.content == "final answer"

            # The model of turn 3 sees the cancelled round after the
            # checkpoint, including its unfinished tool call.
            third_context = invoker.contexts[-1]
            assert [message.role for message in third_context.messages] == [
                "user",
                "assistant",
                "user",
                "assistant",
            ]
            assert "<user-message>second</user-message>" in (
                third_context.messages[2].content
            )
            assert third_context.messages[3].tool_calls[0].call_id == "probe-cancel"

            healed = manager.load(session_ref.session_id)
            agent_context = context_from_dict(
                healed.metadata["agent_context"], registry=LARA_CONTEXT_CODECS
            )
            assert isinstance(agent_context, LaraContext)
            assert [message.role for message in agent_context.messages] == [
                "user",
                "assistant",
                "user",
                "assistant",
                "user",
                "assistant",
            ]
            assert healed.metadata["agent_context_history_count"] == len(
                healed.history
            )
        finally:
            await service.close()
