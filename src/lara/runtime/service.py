"""LaraRuntimeService — the lora adapter over the pygent SDK runtime.

The runtime itself lives in ``pygent.agent.pygent_v1.runtime.service``; this
module keeps the ``lara_api`` call surface (``send_steering``,
``cancel_turn``, ``compress_session``) and the module-level symbols tests
import, delegating everything else to the SDK service.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Sequence, cast

from pygent.agent import (
    REACT_PROJECTION_OPERATION_KIND,
    StandaloneUserMessage,
    SteeringMode,
    encode_react_projection_operation,
)
from pygent.agent.pygent_v1.runtime.service import (
    PROJECTION_OPERATION_METADATA_KEY,
    PygentRuntimeService,
)
from pygent.runtime import ExecutionOptions
from pygent.runtime.codec import invocation_from_dict, message_to_dict
from pygent.tool.executors import ToolExecutionContext
from pygent import UserMessage

from pygent.agent.pygent_v1.runtime.attachments import build_media_blocks, resolve_attachments
from lara.runtime.compression import SessionCompressionModule
from lara.runtime.model_configuration import lara_credential_environment
from lara.schema import CaseRunRef
from pygent.agent.pygent_v1.agents import AgentLimits
from pygent.agent.pygent_v1.graph.common import DEFAULT_REACT_MAX_STEPS
from pygent.agent.pygent_v1.runtime.eternal_conversation import eternal_enabled_for
from pygent.agent.pygent_v1.runtime.realtime.speak import SILENT_ACKNOWLEDGEMENT, parse_speak_args
from lara.tracing import DiffTool, EventStore
from lara.runtime.context import LaraContext
from lara.sessions import SessionManager, realtime_enabled_for
from pygent.tool import ToolSpec
from lara.core.io import plain_object
from pygent.agent.pygent_v1.graph.common import _to_pygent_message
from pygent.agent.pygent_v1.graph.pipeline import checkpoint_conversation_message
from lara.runtime.realtime.feishu import load_feishu_channels  # noqa: F401 — tests patch this name

TURN_DEADLINE_SECONDS = 24 * 60 * 60

def _uncovered_conversation_messages(
    history: list[dict[str, Any]], covered_through: int
) -> tuple[Any, ...]:
    """Keep the uncovered suffix, including its complete opening user turn.

    When the snapshot covers everything, retain the latest exchange so short
    follow-ups still have their immediate conversational referent.
    """
    if not 0 <= covered_through <= len(history):
        raise ValueError("memory coverage is outside the persisted conversation")
    start = covered_through
    if start == len(history) and start:
        start -= 1
    while start > 0 and history[start].get("role") != "user":
        start -= 1
    return tuple(
        message
        for item in history[start:]
        if (message := _to_pygent_message(item)) is not None
    )


class _SpeakExecutor:
    """Delivers the model's speak calls as user-facing stream events (D-RT10).

    The event (``lara.realtime.speak``) is the bubble the desktop renders;
    ``say_nothing`` emits nothing and acknowledges silently. Attachments are
    validated against the workspace and reported as workspace-relative paths.
    Configured external channels (e.g. Feishu) receive the same text
    fire-and-forget — channel failures never affect the tool result.
    """

    def __init__(self, service: "LaraRuntimeService", channels: Sequence[Any] = ()) -> None:
        self.service = service
        self.channels = tuple(channels)
        self._delivery_tasks: set[asyncio.Task[bool]] = set()

    async def execute(
        self, spec: ToolSpec, call: Any, context: ToolExecutionContext
    ) -> object:
        del spec
        args = parse_speak_args(plain_object(call.arguments))
        if args.say_nothing:
            return SILENT_ACKNOWLEDGEMENT
        if context.emit is None:  # pragma: no cover - managed executions carry emit
            raise RuntimeError("speak delivery requires an execution event emitter")
        attachments: list[str] = []
        if args.attachments:
            resolved = resolve_attachments(
                args.attachments, self.service.config.workspace_root
            )
            attachments = [item.relative for item in resolved]
        await context.emit(
            kind="pygent.v1.realtime.speak",
            data={
                "text": args.text or "",
                "attachments": attachments,
            },
        )
        if self.channels:
            text = args.text or ""
            if attachments:
                text = "\n".join([text, *attachments]) if text else "\n".join(attachments)
            for channel in self.channels:
                task = asyncio.create_task(
                    asyncio.to_thread(channel.deliver, text)
                )
                self._delivery_tasks.add(task)
                task.add_done_callback(self._delivery_tasks.discard)
        return "delivered"


class _DiffExecutor:
    def __init__(self, service: "LaraRuntimeService") -> None:
        self.service = service

    async def execute(
        self, spec: ToolSpec, call: Any, context: ToolExecutionContext
    ) -> object:
        del spec
        if context.execution_id is None:
            raise RuntimeError("managed diff execution requires an execution id")
        execution_context = await self.service._turn_context(context.execution_id)
        diff = DiffTool(
            case_run_ref=execution_context.case_run_ref,
            workspace_root=self.service.config.workspace_root,
            turn_id=execution_context.turn_id,
        )
        return await diff.forward(**plain_object(call.arguments))


MEMORY_JOB_LIMITS = AgentLimits(
    max_steps=DEFAULT_REACT_MAX_STEPS,
    max_model_calls=DEFAULT_REACT_MAX_STEPS,
    max_tool_calls=DEFAULT_REACT_MAX_STEPS,
)


class SessionCompressionUnavailable(RuntimeError):
    """Manual compression cannot run for this session/configuration."""


class LaraRuntimeService(PygentRuntimeService):
    """``lara_api`` facing runtime: SDK service + the three legacy entries."""

    def __init__(self, config: Any, *, collaboration: Any = None, dispatch: Any = None, **kwargs: Any) -> None:
        # collaboration 按 B 决策由 lora 自研重写；SDK 服务不接受该形参，
        # 这里接收暂存，保持池工厂签名不断裂。
        # SDK 内核只读进程环境变量；lora 的 credentials.env + keyring 通过
        # 注入的 CredentialEnvironment 提供给模型调用。
        kwargs.setdefault(
            "credential_environ",
            lara_credential_environment(config.user_pygent_root),
        )
        super().__init__(config, **kwargs)
        self._collaboration_hook = collaboration
        self._dispatch_hook = dispatch

    async def send_steering(
        self,
        execution_id: str,
        *,
        input_id: str,
        message: str | None,
        case_run_id: str,
        attachments: Sequence[str] = (),
    ) -> Any:

        resolved_attachments: tuple = ()
        if attachments:
            resolved_attachments = resolve_attachments(
                attachments, self.config.workspace_root
            )
        handle = await self.runtime.get_execution_handle(execution_id)
        return await handle.send_input(
            input_id=input_id,
            kind=REACT_PROJECTION_OPERATION_KIND,
            value=encode_react_projection_operation(
                StandaloneUserMessage(
                    UserMessage(
                        content=message or "",
                        kind="pygent.v1.user.steering",
                        data={
                            "input_id": input_id,
                            "case_run_id": case_run_id,
                            **(
                                {
                                    "attachments": [
                                        item.relative for item in resolved_attachments
                                    ]
                                }
                                if resolved_attachments
                                else {}
                            ),
                        },
                        media=build_media_blocks(resolved_attachments),
                    ),
                    mode=(
                        SteeringMode.IMMEDIATE
                        if self.config.runtime_steering.mode == "immediate"
                        else SteeringMode.WAIT
                    ),
                )
            ),
        )

    async def cancel_turn(self, execution_id: str) -> bool:
        """Request cancellation of one running execution."""

        await self.initialize()
        handle = await self.runtime.get_execution_handle(execution_id)
        return await handle.cancel()

    async def compress_session(
        self,
        *,
        manager,
        session_id: str,
        request_id: str,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        await self.initialize()
        session = manager.load(session_id)
        if eternal_enabled_for(session, self.config):
            raise SessionCompressionUnavailable(
                "eternal conversation sessions manage context with memory "
                "snapshots and do not compress"
            )
        if (
            not self.config.context_compression_enabled
            or not isinstance(self.config.context_window, int)
        ):
            raise SessionCompressionUnavailable(
                "context compression is disabled in the runtime configuration"
            )
        group_name, selected_model_key = manager.model_selection(session_id)
        agent = self.new_agent(
            interactive_approvals=False,
            model_group_name=group_name,
            selected_model_key=selected_model_key,
            eternal_memory_enabled=False,
        )
        if agent.llm is None:
            raise SessionCompressionUnavailable(
                f"agent alias {self.config.agent_alias!r} has no configured model"
            )
        context = self._restored_session_context(
            session=session,
            run_ref=CaseRunRef(
                session_id=session_id,
                case_id=str(session.metadata.get("active_case_id") or "compress"),
                case_run_id=request_id,
                run_dir=str(Path(manager.sessions_root) / session_id),
            ),
            turn_id=request_id,
            config=self.config,
        )
        if not context.messages:
            raise SessionCompressionUnavailable(
                "session context has no projected history to compress"
            )
        bound = await self.bind(SessionCompressionModule(agent), agent)
        handle = await bound.start(
            UserMessage(content="compress context", kind="lara.session.compress"),
            context,
            execution=ExecutionOptions(
                request_id=request_id,
                idempotency_key=request_id,
                deadline=deadline or (time.monotonic() + TURN_DEADLINE_SECONDS),
            ),
        )
        output, _ = await handle.result()
        compressed = output.data
        return {
            "status": "compressed",
            "compression_count": compressed["compression_count"],
            "message_count": len(compressed["messages"]),
        }

    async def execute_case(
        self,
        *,
        manager: SessionManager,
        session: Any,
        case: Any,
        run_ref: CaseRunRef,
    ) -> dict[str, Any]:
        await self.initialize()

        store = EventStore(run_ref)
        store.append(
            "case.started",
            actor="system",
            payload={"title": case.title},
            turn_id="turn-0001",
        )
        carry_context = case.session.get("carry_context") is not False
        if not carry_context:
            session.history = []
        inputs = case.input.get("messages")
        messages = (
            [
                str(item.get("content") or "")
                for item in inputs
                if isinstance(item, dict) and item.get("role", "user") == "user"
            ]
            if isinstance(inputs, list)
            else [str(case.input.get("content") or "")]
        )
        messages = [item for item in messages if item]
        current_message = messages[-1] if messages else ""
        checkpoint_context = LaraContext(
            session_id=run_ref.session_id,
            case_id=run_ref.case_id,
            case_run_id=run_ref.case_run_id,
            run_dir=run_ref.run_dir,
            turn_id="turn-0001",
            system_prompt=session.system_prompt,
            metadata={"persist_conversation_history": carry_context},
        )
        for index, prior in enumerate(messages[:-1]):
            prior_message = UserMessage(content=prior)
            await checkpoint_conversation_message(
                self.config,
                checkpoint_context,
                prior_message,
                boundary=f"case-input-{index}",
            )
            if not carry_context:
                session.history.append(message_to_dict(prior_message))
        if carry_context and messages[:-1]:
            session = manager.load(run_ref.session_id)
        group_name, selected_model_key = manager.model_selection(run_ref.session_id)
        session = manager.load(run_ref.session_id)
        agent = self.new_agent(
            interactive_approvals=False,
            model_group_name=group_name,
            selected_model_key=selected_model_key,
            eternal_memory_enabled=eternal_enabled_for(session, self.config),
            realtime_interaction_enabled=realtime_enabled_for(session),
        )
        turn_message, turn_context = await self._prepare_turn(
            manager=manager,
            run_ref=run_ref,
            message=current_message,
            config=self.config,
            turn_id="turn-0001",
            session=session,
            carry_context=carry_context,
        )
        bound = await self.bind(agent, agent)
        try:
            handle = await self._start_agent_execution(
                bound,
                turn_message,
                turn_context,
                execution=ExecutionOptions(
                    request_id=run_ref.case_run_id,
                    idempotency_key=run_ref.case_run_id,
                    identity=run_ref.session_id,
                    model_calls=(
                        self._model_calls_option(group_name, selected_model_key)
                        if agent.llm is not None
                        else {}
                    ),
                    deadline=time.monotonic() + TURN_DEADLINE_SECONDS,
                ),
            )
        except BaseException:
            await self.reminders.release_initial(run_ref.session_id, "turn-0001")
            raise
        await self.reminders.acknowledge_initial(
            run_ref.session_id,
            "turn-0001",
            handle.execution_id,
        )
        self._track_turn_context(handle.execution_id, turn_context)
        output, _ = await handle.result()
        result = plain_object(plain_object(output.data).get("result"))
        result["event_count"] = len(store.list_by_run())
        store.append(
            "case.finished",
            actor="system",
            payload={"status": result.get("status"), "error": result.get("error")},
            turn_id="turn-0001",
        )
        result["runtime_execution_id"] = handle.execution_id
        self._record_execution_id(run_ref, handle.execution_id)
        return result

    async def get_task(self, task_id: str) -> Any:
        return await self.runtime.get_tool_task(task_id)

    async def get_task_output(self, task_id: str) -> Any:
        return await self.runtime.get_tool_output(task_id)

    async def get_task_result(self, task_id: str) -> Any:
        return await self.runtime.get_tool_result(task_id)

    async def cancel_task(self, task_id: str) -> bool:
        return await self.runtime.cancel_tool_task(task_id)

    async def recovery_case_run(self, execution_id: str) -> CaseRunRef:
        """Recover the application run identity stored with a Pygent invocation."""

        await self.initialize()
        stored = await self.history.get_execution(execution_id)
        if stored is None:
            raise KeyError(f"unknown durable execution {execution_id!r}")
        _, decoded_context = invocation_from_dict(
            stored.input,
            registry=self.runtime.context_codec_registry,
        )
        context = cast(LaraContext, decoded_context)
        return context.case_run_ref
