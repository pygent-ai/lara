from __future__ import annotations

import time
from pathlib import Path
from typing import Any, cast

import pytest
from pygent import AIMessage, ToolCall, ToolMessage, UserMessage
from pygent.llm import ModelExecution, ModelProviderResponse

from lara.config import load_run_config
from lara.core.io import plain_data
from lara.dispatch import DispatchItem, DispatchItemState
from tests.unit.test_model_configuration import native_runtime_config
from lara.runtime.agent_collaboration import AGENT_WAIT_TOOL_SPEC
from lara.runtime.service import LaraRuntimeService
from lara.sessions import (
    AgentMessage,
    AgentMessageState,
    CollaborationOperation,
    CollaborationState,
    SessionManager,
)


def test_agent_wait_schema_is_openai_compatible() -> None:
    parameters = plain_data(AGENT_WAIT_TOOL_SPEC.definition.parameters)

    assert isinstance(parameters, dict)
    assert parameters["type"] == "object"
    assert "oneOf" not in parameters
    assert "anyOf" not in parameters
    properties = parameters["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {
        "collaboration_id",
        "collaboration_ids",
        "timeout_seconds",
    }


def _operation(
    *,
    state: CollaborationState,
    parent_session_id: str,
    agent_alias: str,
    operation_id: str = "op-related",
) -> CollaborationOperation:
    return CollaborationOperation(
        operation_id=operation_id,
        submission_id="start-call",
        source_session_id=parent_session_id,
        target_session_id="child-session",
        agent_alias=agent_alias,
        case_id="collaboration",
        message="research",
        state=state,
        created_at="now",
        updated_at="now",
        final_answer="done" if state.terminal else "",
    )


def _dispatch_item(
    *,
    source_session_id: str,
    item_id: str = "op-related",
) -> DispatchItem:
    return DispatchItem(
        item_id=item_id,
        kind="start",
        workspace_root="/workspace",
        agent_alias="default",
        case_id="collaboration",
        message="research",
        state=DispatchItemState.QUEUED,
        created_at="now",
        updated_at="now",
        source_session_id=source_session_id,
        submission_id="start-call",
    )


class _Dispatch:
    def __init__(self, parent_session_id: str) -> None:
        self.parent_session_id = parent_session_id
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def submit_start(self, **kwargs: Any) -> DispatchItem:
        self.calls.append(("submit_start", kwargs))
        return _dispatch_item(source_session_id=self.parent_session_id)


class _Collaboration:
    def __init__(self, parent_session_id: str, agent_alias: str) -> None:
        self.parent_session_id = parent_session_id
        self.agent_alias = agent_alias
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def send(self, **kwargs: Any) -> AgentMessage:
        self.calls.append(("send", kwargs))
        return AgentMessage(
            message_id="msg-related",
            submission_id="send-call",
            source_session_id=self.parent_session_id,
            source_agent_alias=self.agent_alias,
            target_session_id="child-session",
            content="more context",
            state=AgentMessageState.QUEUED,
            created_at="now",
            updated_at="now",
        )

    async def item_status(self, **kwargs: Any) -> AgentMessage | CollaborationOperation:
        self.calls.append(("item_status", kwargs))
        operation_id = str(kwargs["collaboration_id"])
        return _operation(
            state=(
                CollaborationState.PASSED
                if operation_id == "op-ready"
                else CollaborationState.RUNNING
            ),
            parent_session_id=(
                "foreign-session"
                if operation_id == "op-foreign"
                else self.parent_session_id
            ),
            agent_alias=self.agent_alias,
            operation_id=operation_id,
        )

    async def list_operations(self, **kwargs: Any) -> list[CollaborationOperation]:
        self.calls.append(("list_operations", kwargs))
        return [
            _operation(
                state=CollaborationState.RUNNING,
                parent_session_id=self.parent_session_id,
                agent_alias=self.agent_alias,
            )
        ]

    async def wait_any(
        self, **kwargs: Any
    ) -> tuple[tuple[CollaborationOperation, ...], bool]:
        self.calls.append(("wait_any", kwargs))
        return (
            tuple(
                _operation(
                    state=(
                        CollaborationState.PASSED
                        if operation_id in {"op-related", "op-ready"}
                        else CollaborationState.RUNNING
                    ),
                    parent_session_id=self.parent_session_id,
                    agent_alias=self.agent_alias,
                    operation_id=operation_id,
                )
                for operation_id in kwargs["collaboration_ids"]
            ),
            False,
        )


class _Invoker:
    def __init__(self, agent_alias: str) -> None:
        self.agent_alias = agent_alias
        self.requests: list[tuple[Any, Any]] = []

    def validate_model(self, _model: Any) -> None:
        return None

    def execute(self, *, message: Any, context: Any, **_kwargs: Any) -> ModelExecution:
        self.requests.append((message, context))

        async def invoke(_emit: Any) -> ModelProviderResponse:
            if isinstance(message, UserMessage):
                answer = AIMessage(
                    tool_calls=(
                        ToolCall(
                            call_id="start-call",
                            idempotency_key="start-call",
                            name="agent_start",
                            arguments={"agent": self.agent_alias, "task": "research"},
                        ),
                        ToolCall(
                            call_id="send-call",
                            idempotency_key="send-call",
                            name="agent_send",
                            arguments={
                                "session_id": "child-session",
                                "message": "more context",
                            },
                        ),
                        ToolCall(
                            call_id="status-call",
                            name="agent_status",
                            arguments={"collaboration_id": "op-related"},
                        ),
                        ToolCall(
                            call_id="list-call",
                            name="agent_list",
                            arguments={},
                        ),
                        ToolCall(
                            call_id="wait-single-call",
                            name="agent_wait",
                            arguments={
                                "collaboration_id": "op-related",
                                "timeout_seconds": 1,
                            },
                        ),
                        ToolCall(
                            call_id="wait-any-call",
                            name="agent_wait",
                            arguments={
                                "collaboration_ids": ["op-related", "op-ready"],
                                "timeout_seconds": 1,
                            },
                        ),
                    )
                )
            elif isinstance(message, ToolMessage):
                answer = AIMessage(content="collaboration tools observed")
            else:  # pragma: no cover - native ReAct message contract
                raise AssertionError(type(message).__name__)
            return ModelProviderResponse(message=answer, usage={})

        return ModelExecution(invoke)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_model_tools_share_the_injected_session_collaboration_service(
    tmp_path: Path,
) -> None:
    config = native_runtime_config(tmp_path)
    assert config.resolved_agent is not None
    agent_alias = config.resolved_agent.alias
    config.delegation.allowed_agents = (agent_alias,)
    config.runtime_approvals.enabled = False

    manager = SessionManager(config)
    parent = manager.create("parent", mode="agent")
    run = manager.start_case_run(parent.session_id, "parent", run_config=config)
    collaboration = _Collaboration(parent.session_id, agent_alias)
    dispatch = _Dispatch(parent.session_id)
    invoker = _Invoker(agent_alias)
    service = LaraRuntimeService(
        config,
        collaboration=cast(Any, collaboration),
        dispatch=cast(Any, dispatch),
    )
    service._model_invokers[config.resolved_agent.alias] = invoker
    for agent in service._agent_definitions.values():
        agent.llm = invoker
    try:
        handle = await service.start_turn(
            manager=manager,
            message="coordinate the child",
            run_ref=run,
            turn_id="parent-turn",
            interactive_approvals=False,
            deadline=time.monotonic() + 60,
        )
        output, _ = await handle.result()
    finally:
        manager.finish_case_run(run, "passed")
        await service.close()

    assert output.content == "collaboration tools observed"
    assert len(invoker.requests) == 2
    tool_message = invoker.requests[1][0]
    assert isinstance(tool_message, ToolMessage)
    assert [result.status for result in tool_message.results] == [
        "succeeded",
        "succeeded",
        "succeeded",
        "succeeded",
        "succeeded",
        "succeeded",
    ], repr(tool_message.results)
    started = plain_data(tool_message.results[0].output)
    assert started["operation_id"] == "op-related"
    assert started["status"] == "queued"
    assert started["source_session_id"] == parent.session_id
    listed = plain_data(tool_message.results[3].output)
    assert [item["operation_id"] for item in listed["operations"]] == ["op-related"]
    waited = plain_data(tool_message.results[5].output)
    assert [item["operation_id"] for item in waited["ready"]] == ["op-ready"]
    assert [item["operation_id"] for item in waited["pending"]] == ["op-related"]
    assert waited["timed_out"] is False
    calls = {name: values for name, values in dispatch.calls}
    assert calls["submit_start"]["source_session_id"] == parent.session_id
    assert calls["submit_start"]["submission_id"] == "start-call"
    calls = {name: values for name, values in collaboration.calls}
    assert calls["send"]["source_session_id"] == parent.session_id
    assert calls["send"]["submission_id"] == "send-call"
    assert calls["list_operations"]["session_id"] == parent.session_id
    assert calls["wait_any"]["timeout"] == 1


@pytest.mark.asyncio
async def test_wait_any_checks_every_id_before_waiting(tmp_path: Path) -> None:
    config = native_runtime_config(tmp_path)
    assert config.resolved_agent is not None
    agent_alias = config.resolved_agent.alias
    config.delegation.allowed_agents = (agent_alias,)
    parent = SessionManager(config).create("parent", mode="agent")
    collaboration = _Collaboration(parent.session_id, agent_alias)
    dispatch = _Dispatch(parent.session_id)
    service = LaraRuntimeService(
        config,
        collaboration=cast(Any, collaboration),
        dispatch=cast(Any, dispatch),
    )
    try:
        with pytest.raises(PermissionError, match="outside this collaboration"):
            await service._wait_for_agent_collaborations(
                cast(Any, collaboration),
                ("op-related", "op-foreign"),
                parent.session_id,
                timeout=1,
                multiple=True,
            )
    finally:
        await service.close()

    # Every id is status-checked first; the foreign one fails the check before
    # anything is waited on or submitted.
    assert [name for name, _kwargs in collaboration.calls] == [
        "item_status",
        "item_status",
    ]
    assert dispatch.calls == []
