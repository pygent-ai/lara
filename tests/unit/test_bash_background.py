from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
from pygent import AIMessage, ToolCall, ToolResult, freeze_json
from pygent.tool import ToolTask, ToolTaskState

from lara.config import load_run_config
from tests.unit.test_model_configuration import native_runtime_config
from lara.core.io import plain_object, write_json_atomic
from lara.runtime.bash_tasks import BashTaskObservations
from lara.runtime.context import LaraContext
from lara.runtime.service import LaraRuntimeService
from lara.runtime.tools import ToolObserver
from lara.schema import CaseRunRef
from lara.tracing import EventStore
from lara.sessions import SessionManager


def test_detached_result_preserves_task_and_output_without_error(tmp_path: Path) -> None:
    run = CaseRunRef(session_id="s", case_id="c", case_run_id="r", run_dir=str(tmp_path / "run"))
    observer = ToolObserver(EventStore(run), workspace_root=tmp_path,
                            track_file_effects=True, defer_file_effects=True)
    result = ToolResult(call_id="bash-1", name="bash", status="detached", output="started",
                        task=ToolTask(task_id="task-1", call_id="bash-1",
                                      tool_id="standard.shell.bash", version="3.1.0",
                                      state=ToolTaskState.RUNNING))
    payload, job = observer.record_framework_result(
        "bash", {"command": "sleep 1; echo done > output.txt"}, "turn-1", result,
    )
    assert payload["status"] == "running"
    assert payload["task"]["task_id"] == "task-1"
    assert payload["result"] == "started"
    assert "error" not in payload
    assert job is None  # A running command must not finalize its file effects.
    saved = list(EventStore.iter_jsonl(Path(run.run_dir) / "tool_results.jsonl"))
    assert saved[0]["task"]["task_id"] == "task-1"


@pytest.mark.parametrize("name,status", [
    ("bash", "detached"), ("bash", "unknown"),
    ("tool_task_get", "succeeded"), ("tool_task_stop", "succeeded"),
])
def test_background_output_keeps_existing_preview_limits(
    tmp_path: Path, name: str, status: Literal["detached", "unknown", "succeeded"],
) -> None:
    run = CaseRunRef(session_id="s", case_id="c", case_run_id="r", run_dir=str(tmp_path / "run"))
    observer = ToolObserver(EventStore(run))
    text = "diagnostic " * 3000
    output = text if name == "bash" else {"output": text, "result": {"output": text}}
    result = ToolResult(call_id="large", name=name, status=status, output=freeze_json(output),
                            task=ToolTask(task_id="large-task", call_id="large",
                                      tool_id="standard.shell.bash", version="3.1.0", state=ToolTaskState.RUNNING))
    payload, _ = observer.record_framework_result(name, {}, "turn-1", result)
    assert "diagnostic" in json.dumps(payload)
    assert text not in json.dumps(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_seconds", [0, 0.05])
async def test_managed_bash_background_can_be_queried_stopped_and_read_after_restart(tmp_path: Path, wait_seconds: float) -> None:
    config = native_runtime_config(tmp_path)
    service = LaraRuntimeService(config, tool_max_concurrency=1)
    task_id = None
    try:
        await service.initialize()
        agent = service.new_agent(interactive_approvals=False)
        tools = agent.new_tool_layer()
        bound = service.runtime.bind(tools, binding=service.binding)
        context = LaraContext(tools=agent.tool_definitions)
        answer, _ = await bound.invoke(AIMessage(tool_calls=(ToolCall(
            call_id="start", name="bash",
            arguments={"command": "echo started; sleep 30", "timeout": wait_seconds},
        ),)), context)
        result = answer.results[0]
        assert result.status == "detached", result
        assert result.task is not None
        task_id = result.task.task_id
        async with asyncio.timeout(5):
            while "started" not in str(await service.get_task_output(task_id)):
                await asyncio.sleep(0.02)
        # Control calls must not wait behind the only occupied tool permit.
        queried, _ = await asyncio.wait_for(bound.invoke(AIMessage(tool_calls=(ToolCall(
            call_id="query", name="tool_task_get", arguments={"task_id": task_id},
        ),)), context), 5)
        assert queried.results[0].status == "succeeded"
        assert "started" in str(queried.results[0].output)
        stopped, _ = await asyncio.wait_for(bound.invoke(AIMessage(tool_calls=(ToolCall(
            call_id="stop", name="tool_task_stop", arguments={"task_id": task_id},
        ),)), context), 10)
        assert stopped.results[0].status == "succeeded"
        assert plain_object(stopped.results[0].output)["cancel_requested"] is True
        final = await service.get_task_result(task_id)
        assert final is not None and final.status != "succeeded"
    finally:
        await service.close()
    restarted = LaraRuntimeService(config)
    try:
        await restarted.initialize()
        assert await restarted.get_task(task_id) is not None
        assert await restarted.get_task_result(task_id) is not None
        assert "started" in str(await restarted.get_task_output(task_id))
    finally:
        await restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["complete", "cancel", "shutdown", "restore"])
async def test_background_completion_finalizes_audit_and_late_file_writes(tmp_path: Path, finish: str) -> None:
    from lara.runtime.agent.pipeline import ToolAuditModule
    from lara.runtime.bash_tasks import BashTaskObservations

    config = native_runtime_config(tmp_path)
    manager = SessionManager(config)
    session = manager.create("chat", mode="chat")
    run = manager.start_case_run(session.session_id, "chat", run_config=config)
    service = LaraRuntimeService(config)
    try:
        await service.initialize()
        agent = service.new_agent(interactive_approvals=False)
        context = LaraContext(session_id=run.session_id, case_id=run.case_id,
                              case_run_id=run.case_run_id, run_dir=str(run.run_dir),
                              turn_id="turn-1", tools=agent.tool_definitions)
        await service.bash_tasks.prepare(context)
        command = ("sleep 0.5; echo final > late.txt" if finish in {"complete", "restore"}
                   else "echo diagnostic; echo partial > late.txt; sleep 30")
        message = AIMessage(tool_calls=(ToolCall(call_id="late-write", name="bash",
            arguments={"command": command, "timeout": 0}),))
        bound = service.runtime.bind(agent.new_tool_layer(), binding=service.binding)
        answer, _ = await bound.invoke(message, context)
        assert answer.results[0].status == "detached"
        audit = ToolAuditModule(config, bash_tasks=service.bash_tasks)
        audited, _ = await service.runtime.bind(audit, binding=service.binding).invoke(
            answer, context + message,
        )
        # Audit records the framework result without rewriting the model-visible
        # output; the audited status is asserted from tool_results.jsonl below.
        assert audited.results[0].output == answer.results[0].output
        if finish == "restore":
            previous = service.bash_tasks
            previous._closed = True
            for task in previous._observers.values():
                task.cancel()
            await asyncio.gather(*previous._observers.values(), return_exceptions=True)
            service.bash_tasks = BashTaskObservations(config, service.runtime, previous.directory)
            await service.bash_tasks.restore()
        elif finish in {"cancel", "shutdown"}:
            async with asyncio.timeout(5):
                while not (tmp_path / "late.txt").exists():
                    await asyncio.sleep(0.02)
            if finish == "cancel":
                assert answer.results[0].task is not None
                assert await service.cancel_task(answer.results[0].task.task_id)
            else:
                await service.close()
        # The foreground invocation has returned. The task and its finalizer continue.
        async with asyncio.timeout(10):
            while not (Path(run.run_dir) / "file_events.jsonl").exists():
                await asyncio.sleep(0.02)
        events = list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl"))
        assert any(Path(event["path"]).name == "late.txt" for event in events)
        results = list(EventStore.iter_jsonl(Path(run.run_dir) / "tool_results.jsonl"))
        expected = "success" if finish in {"complete", "restore"} else "error"
        assert [item["status"] for item in results] == ["running", expected]
        assert results[0]["tool_call_id"] == results[1]["tool_call_id"]
        if finish in {"cancel", "shutdown"}:
            assert "diagnostic" in str(results[1]["result"])
        assert len(list(EventStore.iter_jsonl(Path(run.run_dir) / "tool_calls.jsonl"))) == 1
        if finish in {"complete", "cancel"}:
            # The lifecycle event is written before the task resolves, so the
            # observer's file-effect output below cannot race ahead of it.
            traced = [
                row["type"]
                for row in EventStore.iter_jsonl(Path(run.run_dir) / "events.jsonl")
            ]
            # Cancelling a running command can only report an uncertain state
            # on some platforms; pygent picks cancelled or unknown accordingly.
            expected = {
                "complete": ("completed",),
                "cancel": ("cancelled", "unknown"),
            }[finish]
            assert any(f"runtime.tool_task.{suffix}" in traced for suffix in expected)
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {"is_background": True}, {"timeout": 0}, {"timeout": -1},
])
async def test_bash_background_never_bypasses_approval(tmp_path: Path, arguments: dict) -> None:
    config = native_runtime_config(tmp_path)
    config.runtime_approvals.enabled = True
    config.runtime_approvals.preauthorized_tools = ()
    service = LaraRuntimeService(config)
    try:
        await service.initialize()
        agent = service.new_agent(interactive_approvals=False)
        bound = service.runtime.bind(agent.new_tool_layer(), binding=service.binding)
        answer, _ = await bound.invoke(AIMessage(tool_calls=(ToolCall(
            call_id="forbidden", name="bash",
            arguments={"command": "echo forbidden > forbidden.txt", **arguments},
        ),)), LaraContext(tools=agent.tool_definitions))
        assert answer.results[0].status == "rejected"
        assert not (tmp_path / "forbidden.txt").exists()
    finally:
        await service.close()


async def test_tool_task_events_trace_into_owning_case_run(tmp_path: Path) -> None:
    run = CaseRunRef(session_id="s", case_id="c", case_run_id="r", run_dir=str(tmp_path / "run"))
    directory = tmp_path / "obs"
    directory.mkdir()

    async def no_result(task_id: str, *, wait: bool = False) -> None:
        return None

    observations = BashTaskObservations(
        native_runtime_config(tmp_path),
        cast(Any, SimpleNamespace(get_tool_result=no_result)),
        directory,
    )
    task_id = "task-sink-1"
    identity = hashlib.sha256(task_id.encode()).hexdigest()
    write_json_atomic(directory / f"{identity}.json", {
        "task_id": task_id,
        "case_run_ref": run.to_dict(),
        "turn_id": "turn-1",
        "audit_call_id": "bash-1",
        "arguments": {"command": "sleep 1"},
    })

    # Unknown event kinds and unattributed tasks are ignored, and a fresh
    # start is emitted before the observation record exists, so it is skipped.
    await observations.on_tool_task_event("tool.progress", {"task_id": task_id})
    await observations.on_tool_task_event("tool.task.started", {"task_id": "untracked"})
    await observations.on_tool_task_event(
        "tool.task.started", {"task_id": task_id, "call_id": "bash-1"},
    )
    await observations.on_tool_task_event("tool.task.completed", {
        "task_id": task_id, "call_id": "bash-1", "tool_id": "standard.shell.bash",
        "status": "succeeded",
    })
    await observations.on_tool_task_event("tool.task.started", {
        "task_id": task_id, "call_id": "bash-1", "attempt": 2, "recovery": True,
    })

    rows = list(EventStore.iter_jsonl(Path(run.run_dir) / "events.jsonl"))
    assert [row["type"] for row in rows] == [
        "runtime.tool_task.completed", "runtime.tool_task.started",
    ]
    completed = rows[0]
    assert completed["turn_id"] == "turn-1"
    assert completed["actor"] == "system"
    assert completed["payload"]["task_id"] == task_id
    assert completed["payload"]["status"] == "succeeded"
    assert rows[1]["payload"]["recovery"] is True

    # After close, no further writes occur.
    await observations.close()
    await observations.on_tool_task_event("tool.task.failed", {
        "task_id": task_id, "call_id": "bash-1", "status": "failed",
    })
    assert len(list(EventStore.iter_jsonl(Path(run.run_dir) / "events.jsonl"))) == 2
