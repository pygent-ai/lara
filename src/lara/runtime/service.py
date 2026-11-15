"""LaraRuntimeService — the lara adapter over the pygent SDK runtime.

The runtime itself lives in ``pygent.agent.pygent_v1.runtime.service``; this
module keeps the ``lara_api`` call surface (``cancel_turn``,
``compress_session``, ``execute_case``, task lookups) and the Feishu speak
channel, delegating everything else to the SDK service.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from pygent import UserMessage
from pygent.agent.pygent_v1.graph.pipeline import checkpoint_conversation_message
from pygent.agent.pygent_v1.runtime.eternal_conversation import eternal_enabled_for
from pygent.agent.pygent_v1.runtime.realtime.speak import SPEAK_TOOL_SPEC
from pygent.agent.pygent_v1.runtime.service import PygentRuntimeService, _SpeakExecutor
from pygent.runtime import ExecutionOptions
from pygent.runtime.codec import context_to_dict, invocation_from_dict, message_to_dict

from lara.config import load_run_config
from lara.core.io import plain_object
from lara.runtime.compression import SessionCompressionModule
from lara.runtime.context import LARA_CONTEXT_CODECS, LaraContext
from lara.runtime.model_configuration import lara_credential_environment
from lara.runtime.realtime.feishu import load_feishu_channels
from lara.schema import CaseRunRef, RunConfig
from lara.sessions import SessionManager, realtime_enabled_for
from lara.tracing import EventStore

TURN_DEADLINE_SECONDS = 24 * 60 * 60


class SessionCompressionUnavailable(RuntimeError):
    """Manual compression cannot run for this session/configuration."""


class LaraRuntimeService(PygentRuntimeService):
    """``lara_api`` facing runtime: SDK service + cancel/compress/case entries."""

    config: RunConfig  # pyright: ignore[reportIncompatibleVariableOverride] — lora's RunConfig subclasses the SDK's

    def __init__(self, config: RunConfig, **kwargs: Any) -> None:
        # SDK 内核只读进程环境变量；lara 的 credentials.env + keyring 通过
        # 注入的 CredentialEnvironment 提供给模型调用。
        kwargs.setdefault(
            "credential_environ",
            lara_credential_environment(config.user_pygent_root),
        )
        kwargs.setdefault("run_config_loader", load_run_config)
        super().__init__(config, **kwargs)
        # The SDK ships no speak delivery channels; lara adds Feishu.
        self.runtime.register_tool(
            SPEAK_TOOL_SPEC,
            _SpeakExecutor(self, channels=load_feishu_channels(config.user_pygent_root)),
            replace_existing=True,
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
        _, compressed = await handle.result()

        # The compressed projection has not been measured by a foreground call
        # yet; the next turn recalibrates the gauge through its real usage.
        session = manager.load(session_id)
        session.metadata["agent_context"] = context_to_dict(
            replace(compressed, last_input_tokens=0),
            registry=LARA_CONTEXT_CODECS,
        )
        session.metadata["agent_context_history_count"] = len(session.history)
        manager.save(session)
        return {
            "compression_count": compressed.compression_count,
            "message_count": len(compressed.messages),
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
