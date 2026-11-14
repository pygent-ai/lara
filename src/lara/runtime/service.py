"""LaraRuntimeService — the lora adapter over the pygent SDK runtime.

The runtime itself lives in ``pygent.agent.pygent_v1.runtime.service``; this
module keeps the ``lara_api`` call surface (``send_steering``,
``cancel_turn``, ``compress_session``, ``execute_case``), the Feishu speak
channel and the agent collaboration tool face, delegating everything else to
the SDK service.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence, cast

from pygent.agent import (
    REACT_PROJECTION_OPERATION_KIND,
    StandaloneUserMessage,
    SteeringMode,
    encode_react_projection_operation,
)
from pygent.agent.pygent_v1.runtime.service import (
    PROJECTION_OPERATION_METADATA_KEY,
    PygentRuntimeService,
    _SpeakExecutor,
)
from pygent.runtime import ExecutionOptions
from pygent.runtime.codec import context_to_dict, invocation_from_dict, message_to_dict
from pygent.tool import AgentToolExecutor, SandboxExecutorSupport
from pygent.tool.executors import ToolExecutionContext
from pygent import UserMessage

from pygent.agent.pygent_v1.runtime.attachments import build_media_blocks, resolve_attachments
from lara.config import load_run_config
from lara.dispatch import DispatchOperationView, DispatchService, DispatchStore
from lara.runtime.agent_collaboration import (
    AGENT_COLLABORATION_TOOL_SPECS,
    visible_agent_collaboration_specs,
)
from lara.runtime.compression import SessionCompressionModule
from lara.runtime.model_configuration import lara_credential_environment
from lara.schema import CaseRunRef, RunConfig
from pygent.agent.pygent_v1.runtime.eternal_conversation import eternal_enabled_for
from pygent.agent.pygent_v1.runtime.realtime.speak import SPEAK_TOOL_SPEC
from lara.tracing import EventStore
from lara.runtime.context import LARA_CONTEXT_CODECS, LaraContext
from lara.sessions import (
    AgentMessage,
    SessionManager,
    agent_message_to_dict,
    collaboration_item_dict,
    realtime_enabled_for,
    user_pygent_root_for,
)
from pygent.tool import ToolSpec
from lara.core.io import plain_object
from pygent.agent.pygent_v1.graph.pipeline import checkpoint_conversation_message
from lara.runtime.realtime.feishu import load_feishu_channels

if TYPE_CHECKING:
    from lara.orchestration.session_collaboration import SessionCollaborationService

TURN_DEADLINE_SECONDS = 24 * 60 * 60

CollaborationItem = AgentMessage | DispatchOperationView

class SessionCompressionUnavailable(RuntimeError):
    """Manual compression cannot run for this session/configuration."""


class LaraRuntimeService(PygentRuntimeService):
    """``lara_api`` facing runtime: SDK service + the three legacy entries."""

    config: RunConfig  # pyright: ignore[reportIncompatibleVariableOverride] — lora's RunConfig subclasses the SDK's

    def __init__(
        self,
        config: RunConfig,
        *,
        collaboration: "SessionCollaborationService | None" = None,
        dispatch: DispatchService | None = None,
        **kwargs: Any,
    ) -> None:
        # SDK 内核只读进程环境变量；lora 的 credentials.env + keyring 通过
        # 注入的 CredentialEnvironment 提供给模型调用。
        kwargs.setdefault(
            "credential_environ",
            lara_credential_environment(config.user_pygent_root),
        )
        super().__init__(config, **kwargs)
        # The SDK ships no speak delivery channels; lora adds Feishu.
        self.runtime.register_tool(
            SPEAK_TOOL_SPEC,
            _SpeakExecutor(self, channels=load_feishu_channels(config.user_pygent_root)),
            replace_existing=True,
        )
        # collaboration（agent 协作）按 B 决策由 lora 自研：工具面、访问
        # 控制和后台 start 的 dispatch 都在这里接到 SDK 的工具注册表上。
        self.collaboration = collaboration
        self._dispatch = dispatch
        collaboration_executor = AgentToolExecutor(
            invoke=self._execute_agent_collaboration
        )
        setattr(
            collaboration_executor,
            "sandbox_support",
            SandboxExecutorSupport(
                profiles=("agent",),
                durable_reconnect=True,
                deployment_fingerprint="lara:agent-collaboration:v1",
            ),
        )
        for spec in AGENT_COLLABORATION_TOOL_SPECS:
            self.runtime.register_tool(spec, collaboration_executor)
        # lora schedules through its own automation layer, so the SDK
        # scheduler tools stay invisible to the model; only the collaboration
        # tools that are actually usable in this runtime are exposed.
        self.external_tools = visible_agent_collaboration_specs(
            config, collaboration_available=collaboration is not None
        )
        # The SDK built its template agent with the SDK tool set in
        # super().__init__; rebuild it so the template sees lora's tools.
        self._agent_definitions.clear()
        if config.resolved_agent is not None and config.model_config is not None:
            self.new_agent(interactive_approvals=False)

    async def _execute_agent_collaboration(
        self,
        spec: ToolSpec,
        call: Any,
        context: ToolExecutionContext,
    ) -> object:
        collaboration = self.collaboration
        if collaboration is None:
            raise RuntimeError("Agent collaboration is unavailable in this runtime")
        actor = await self._agent_tool_context(context)
        arguments = plain_object(call.arguments)
        name = spec.definition.name
        submission_id = str(call.idempotency_key or call.call_id)
        if name == "agent_start":
            agent_alias = str(arguments["agent"])
            if agent_alias not in self.config.delegation.allowed_agents:
                raise PermissionError(
                    f"collaboration with agent {agent_alias!r} is not allowed"
                )
            model_group = arguments.get("model_group")
            child_config = self._child_agent_config(agent_alias)
            if model_group is not None:
                self._require_model_group(child_config, str(model_group))
            # Background starts are dispatched user-globally; the dispatch
            # scheduler claims and drives them outside this process.
            item = await asyncio.to_thread(
                self._dispatch_service().submit_start,
                config=child_config,
                source_session_id=actor.session_id,
                message=str(arguments["task"]),
                submission_id=submission_id,
                model_group=None if model_group is None else str(model_group),
            )
            return DispatchOperationView.from_item(item).to_dict()
        if name == "agent_send":
            # Addressing policy: every session registered in the user-level
            # directory is reachable, across projects included; unknown
            # sessions fail closed inside collaboration.send.
            message = await collaboration.send(
                config=self.config,
                manager=SessionManager(self.config),
                source_session_id=actor.session_id,
                source_agent_alias=self.config.agent_alias,
                target_session_id=str(arguments["session_id"]),
                message=str(arguments["message"]),
                submission_id=submission_id,
            )
            return agent_message_to_dict(message)
        if name == "agent_list":
            operations = await collaboration.list_operations(
                config=self.config,
                session_id=actor.session_id,
            )
            return {
                "operations": [
                    collaboration_item_dict(operation) for operation in operations
                ]
            }
        if name == "agent_status":
            item = await collaboration.item_status(
                config=self.config,
                collaboration_id=str(arguments["collaboration_id"]),
            )
            self._require_collaboration_access(item, actor.session_id)
            return collaboration_item_dict(item)
        if name == "agent_wait":
            timeout = float(arguments.get("timeout_seconds", 30))
            if not 0 < timeout <= 120:
                raise ValueError(
                    "timeout_seconds must be greater than 0 and at most 120"
                )
            has_single = "collaboration_id" in arguments
            multiple = "collaboration_ids" in arguments
            if has_single == multiple:
                raise ValueError(
                    "provide exactly one of collaboration_id or collaboration_ids"
                )
            collaboration_ids = (
                tuple(str(item) for item in arguments["collaboration_ids"])
                if multiple
                else (str(arguments["collaboration_id"]),)
            )
            if not 1 <= len(collaboration_ids) <= 32:
                raise ValueError(
                    "collaboration_ids must contain between 1 and 32 items"
                )
            if len(set(collaboration_ids)) != len(collaboration_ids):
                raise ValueError("collaboration_ids must be unique")
            return await self._wait_for_agent_collaborations(
                collaboration,
                collaboration_ids,
                actor.session_id,
                timeout=timeout,
                multiple=multiple,
            )
        raise ValueError(f"unsupported Agent collaboration tool {name!r}")

    async def _agent_tool_context(self, context: ToolExecutionContext) -> LaraContext:
        if context.execution_id is None:
            raise RuntimeError("Agent collaboration requires an execution id")
        return cast(LaraContext, await self._turn_context(context.execution_id))

    async def _wait_for_agent_collaborations(
        self,
        collaboration: "SessionCollaborationService",
        collaboration_ids: tuple[str, ...],
        actor_session_id: str,
        *,
        timeout: float,
        multiple: bool,
    ) -> dict[str, Any]:
        items = tuple(
            await asyncio.gather(
                *(
                    collaboration.item_status(
                        config=self.config,
                        collaboration_id=collaboration_id,
                    )
                    for collaboration_id in collaboration_ids
                )
            )
        )
        for item in items:
            self._require_collaboration_access(item, actor_session_id)
        if any(item.done for item in items):
            completed = items
            timed_out = False
        else:
            # Dispatched items are driven by the dispatch scheduler; waiting is
            # pure polling, there is no in-process resume.
            completed, timed_out = await collaboration.wait_any(
                config=self.config,
                collaboration_ids=collaboration_ids,
                timeout=timeout,
            )
            for item in completed:
                self._require_collaboration_access(item, actor_session_id)
        if not multiple:
            return {**collaboration_item_dict(completed[0]), "timed_out": timed_out}
        return {
            "ready": [collaboration_item_dict(item) for item in completed if item.done],
            "pending": [
                collaboration_item_dict(item) for item in completed if not item.done
            ],
            "timed_out": timed_out,
        }

    @staticmethod
    def _require_collaboration_access(
        item: CollaborationItem, actor_session_id: str
    ) -> None:
        if actor_session_id not in {item.source_session_id, item.target_session_id}:
            noun = "message" if isinstance(item, AgentMessage) else "operation"
            raise PermissionError(f"Agent {noun} is outside this collaboration")

    @staticmethod
    def _require_model_group(child_config: RunConfig, model_group: str) -> None:
        native = child_config.model_config
        if native is None or model_group not in native.model_groups:
            raise ValueError(
                f"unknown model group {model_group!r} for agent "
                f"{child_config.agent_alias!r}"
            )

    def _child_agent_config(self, alias: str) -> RunConfig:
        # lora's RunConfig carries product-only sections (delegation, ...)
        # the SDK's from_dict rejects, so child configs resolve through lora.
        if alias == self.config.agent_alias:
            return RunConfig.from_dict(self.config.to_dict())
        return load_run_config(
            workspace_root=self.config.workspace_root,
            agent_alias=alias,
            max_steps=self.config.max_steps,
            context_window=self.config.context_window,
        )

    def _dispatch_service(self) -> DispatchService:
        """User-level dispatch service; overridable via the constructor."""
        if self._dispatch is None:
            root = user_pygent_root_for(self.config.pygent_root)
            self._dispatch = DispatchService(
                DispatchStore(Path(root) / "dispatch-v1.sqlite3")
            )
        return self._dispatch

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
