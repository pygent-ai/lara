from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pygent import thaw_json

from lara.core.io import append_jsonl, read_json
from lara.core.process import owner_is_foreign_and_alive
from lara.orchestration import (
    ManagedSessionTurn,
    SessionExecutionCoordinator,
    SessionTurnService,
)
from pygent.lara.runtime.attachments import resolve_attachments
from lara.schema import CaseRunRef, CaseRunStatus
from lara.schema.host_models import status_resumable
from lara.sessions import realtime_enabled_for
from lara_api.container import ApiContext
from lara_api.models.events import ExecutionEvent
from lara_api.models.requests import ChatTurnRequest
from lara_api.services.session_service import finalize_dead_run, session_service_for_scope

CHAT_KEEPALIVE_SECONDS = 10.0

# A resume of a resumable terminal run submits a fresh turn with this input:
# the durable history already carries the task and its partial progress, so
# the model only needs the signal to pick the work back up.
RESUME_TURN_MESSAGE = "继续"
RESUME_TURN_KIND = "lara.user.resume"


def _should_finalize_on_resume_miss(manager: Any, run_ref: CaseRunRef) -> bool:
    """Decide whether a resume miss may record an error terminal for `run_ref`.

    Only a run whose owning process cannot finalize it anymore (this process,
    or a dead owner) is reconciled here; a live foreign owner keeps authority
    over its own terminal verdict.
    """

    if manager.run_timing(run_ref).get("status") != "running":
        return False
    return not owner_is_foreign_and_alive(manager.run_owner(run_ref))


_TERMINAL_EVENTS = {
    "execution.completed",
    "execution.failed",
    "execution.cancelled",
    "execution.deadline_exceeded",
}


def _with_run_timing(
    event: ExecutionEvent, manager: Any, ref: CaseRunRef
) -> ExecutionEvent:
    if event.kind not in _TERMINAL_EVENTS | {"execution.started", "lara.chat.started"}:
        return event
    return event.model_copy(
        update={
            "data": {**event.data, "run_timing": manager.run_timing(ref)},
        }
    )


async def stream_chat_turn(
    context: ApiContext,
    request: ChatTurnRequest,
    *,
    registry: ChatRunRegistry | None = None,
) -> AsyncIterator[str]:
    active_registry = registry if registry is not None else context.chat_registry
    try:
        run = await active_registry.resolve(context, request)
    except Exception as exc:
        yield _sse(_transport_error_event(request.execution_id or "", exc))
        return
    if run is None:
        yield _sse(
            _transport_error_event(
                request.execution_id or "", LookupError("execution not found")
            )
        )
        return
    async for event in run.events(
        after=request.after_sequence,
        log_model_text_deltas=request.log_model_text_deltas,
    ):
        yield _sse(event)


@dataclass(slots=True)
class ForkReplyReceipt:
    """The deliver_steering receipt for the fork-reply path (D-RT2/3)."""

    input_id: str
    reply: dict[str, object]

    @property
    def status(self) -> str:
        return "fork_replied"


@dataclass(slots=True)
class ActiveChatRun:
    """SSE-facing view over a transport-independent managed session turn."""

    turn: ManagedSessionTurn
    subscribers: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def execution_id(self) -> str | None:
        return self.turn.execution_id

    @property
    def execution_handle(self) -> Any | None:
        return self.turn.execution_handle

    @property
    def run_ref(self) -> CaseRunRef | None:
        return self.turn.run_ref

    @property
    def manager(self) -> Any:
        return self.turn.manager

    @property
    def runtime_service(self) -> Any:
        return self.turn.runtime_service

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self.turn.task

    @property
    def done(self) -> bool:
        return self.turn.done

    @property
    def status(self) -> str:
        return self.turn.status

    @property
    def startup_error(self) -> BaseException | None:
        return self.turn.startup_error

    async def events(
        self,
        *,
        after: int | None,
        log_model_text_deltas: bool = False,
    ) -> AsyncIterator[ExecutionEvent]:
        if self.startup_error is not None and self.execution_handle is None:
            yield ExecutionEvent(
                schema_version="1",
                event_id="lara-transport-error",
                execution_id="",
                attempt_id="transport",
                trace_id="transport",
                span_id="transport",
                sequence=0,
                timestamp_unix_ns=0,
                module_path="lara.transport",
                kind="lara.transport.error",
                data={
                    "error": str(self.startup_error),
                    "error_type": type(self.startup_error).__name__,
                },
            )
            return
        handle = self.execution_handle
        if handle is None:
            return
        run_ref = self.run_ref
        if run_ref is None:  # pragma: no cover - ready implies case-run creation
            raise RuntimeError("chat execution has no case run")
        async with self.lock:
            self.subscribers += 1
        try:
            async with handle.subscribe(after=after) as execution_events:
                pending_event: asyncio.Task[Any] | None = None
                try:
                    while True:
                        if pending_event is None:
                            pending_event = asyncio.create_task(anext(execution_events))
                        try:
                            raw = await asyncio.wait_for(
                                asyncio.shield(pending_event),
                                CHAT_KEEPALIVE_SECONDS,
                            )
                        except TimeoutError:
                            yield _keepalive_event(handle.execution_id, after or 0)
                            continue
                        except StopAsyncIteration:
                            pending_event = None
                            break
                        pending_event = None
                        event = _execution_event(raw)
                        if event.kind in _TERMINAL_EVENTS:
                            # Publish completion only after run metadata and history
                            # are durable, so the client's immediate reload is final.
                            await asyncio.shield(self.turn.wait_finalized())
                        event = _with_run_timing(event, self.manager, run_ref)
                        if log_model_text_deltas:
                            _append_model_text_delta(
                                event,
                                stream_dir=Path(run_ref.run_dir),
                            )
                        yield event
                finally:
                    if pending_event is not None and not pending_event.done():
                        pending_event.cancel()
                        await asyncio.gather(pending_event, return_exceptions=True)
            # Publish final session metadata before the client refreshes its view.
            await asyncio.shield(self.turn.wait_finalized())
        finally:
            async with self.lock:
                self.subscribers -= 1


@dataclass(slots=True)
class AttachedExecutionRun:
    execution_handle: Any
    run_ref: CaseRunRef | None = None
    manager: Any | None = None
    lease: Any | None = None
    snapshot: Any = None

    async def events(
        self,
        *,
        after: int | None,
        log_model_text_deltas: bool = False,
    ) -> AsyncIterator[ExecutionEvent]:
        terminated = False
        try:
            async with self.execution_handle.subscribe(after=after) as events:
                pending_event: asyncio.Task[Any] | None = None
                try:
                    while True:
                        if pending_event is None:
                            pending_event = asyncio.create_task(anext(events))
                        try:
                            raw = await asyncio.wait_for(
                                asyncio.shield(pending_event),
                                CHAT_KEEPALIVE_SECONDS,
                            )
                        except TimeoutError:
                            yield _keepalive_event(
                                self.execution_handle.execution_id, after or 0
                            )
                            continue
                        except StopAsyncIteration:
                            pending_event = None
                            break
                        pending_event = None
                        event = _execution_event(raw)
                        if event.kind in _TERMINAL_EVENTS:
                            terminated = True
                        if self.manager is not None and self.run_ref is not None:
                            event = _with_run_timing(event, self.manager, self.run_ref)
                        if log_model_text_deltas and self.run_ref is not None:
                            _append_model_text_delta(
                                event,
                                stream_dir=Path(self.run_ref.run_dir),
                            )
                        yield event
                finally:
                    if pending_event is not None and not pending_event.done():
                        pending_event.cancel()
                        await asyncio.gather(pending_event, return_exceptions=True)
            if (
                not terminated
                and self.snapshot is not None
                and self.snapshot.status.terminal
            ):
                # The subscriber did not deliver a terminal event (its buffer may
                # have been consumed before this client attached). Synthesize one
                # from the durable snapshot so the client can finalize.
                event = _terminal_event_from_snapshot(self.snapshot)
                if self.manager is not None and self.run_ref is not None:
                    event = _with_run_timing(event, self.manager, self.run_ref)
                yield event
        finally:
            if self.lease is not None:
                await self.lease.release()


class ChatRunRegistry:
    """HTTP-facing adapter over the application execution coordinator."""

    def __init__(self, coordinator: SessionExecutionCoordinator | None = None) -> None:
        self.coordinator = coordinator or SessionExecutionCoordinator()

    async def resolve(
        self, context: ApiContext, request: ChatTurnRequest
    ) -> ActiveChatRun | AttachedExecutionRun | None:
        if request.execution_id:
            return await self._resolve_attached_run(context, request)
        if request.resume:
            run = await self._resolve_session_resume(context, request)
            if run is not None:
                return run
            # The last run is a resumable terminal (or was just reconciled as
            # dead): continuing the session with a fresh turn is the only
            # remaining way to resume it.
            return await self._submit_turn(
                context,
                request,
                message=RESUME_TURN_MESSAGE,
                message_kind=RESUME_TURN_KIND,
                message_data={"origin": "resume"},
            )
        if not request.message and not request.attachments:
            raise ValueError("message or attachments is required when execution_id is not provided")
        return await self._submit_turn(
            context, request, message=request.message or ""
        )

    async def _submit_turn(
        self,
        context: ApiContext,
        request: ChatTurnRequest,
        *,
        message: str,
        message_kind: str = "lara.chat.turn",
        message_data: dict[str, object] | None = None,
    ) -> ActiveChatRun:
        service = session_service_for_scope(context, request.scope_id)
        manager = service.manager
        if request.attachments:
            resolve_attachments(
                request.attachments, manager.config.workspace_root
            )
        turn_service = SessionTurnService(
            coordinator=self.coordinator,
            acquire_runtime=context.acquire_runtime,
        )
        turn = await turn_service.submit(
            config=manager.config,
            manager=manager,
            session_id=request.session_id,
            message=message,
            case_id=request.case_id,
            turn_id=request.turn_id,
            model_group_name=request.model_group_name,
            selected_model_key=request.selected_model_key,
            message_kind=message_kind,
            message_data=message_data,
            attachments=request.attachments,
        )
        await turn.wait_ready()
        return ActiveChatRun(turn)

    async def _resolve_session_resume(
        self, context: ApiContext, request: ChatTurnRequest
    ) -> ActiveChatRun | AttachedExecutionRun | None:
        """Resolve `resume` against the session's last case run.

        Returns the run to reattach while the last run is recoverable, and
        None when the last run ended resumably (error, failed, skipped) or
        was just reconciled as dead; the caller then continues the session
        with a fresh turn.
        """

        if not request.session_id:
            raise LookupError("恢复会话需要指定 session_id。")
        service = session_service_for_scope(context, request.scope_id)
        manager = service.manager
        metadata = manager.show(request.session_id)["metadata"]
        status = CaseRunStatus.from_value(metadata.get("last_case_run_status"))
        run_id = metadata.get("last_case_run_id")
        if status is None or not isinstance(run_id, str) or not run_id:
            raise LookupError("会话没有可恢复的运行。")
        if status is CaseRunStatus.RUNNING:
            return await self._reattach_running_run(
                context, request, manager, run_id
            )
        if not status_resumable(status):
            raise LookupError("上次运行已正常完成，无需恢复。")
        return None

    async def _reattach_running_run(
        self,
        context: ApiContext,
        request: ChatTurnRequest,
        manager: Any,
        run_id: str,
    ) -> ActiveChatRun | AttachedExecutionRun | None:
        run_ref = manager.find_case_run(request.session_id, run_id)
        run_metadata = read_json(Path(run_ref.run_dir) / "run_metadata.json")
        execution_id = run_metadata.get("runtime_execution_id")
        if isinstance(execution_id, str) and execution_id:
            run = await self._resolve_attached_run(
                context,
                request.model_copy(update={"execution_id": execution_id}),
            )
            if run is not None:
                return run
            # The attach attempt reconciled the dead run as an error; the
            # continuation turn submitted by the caller resumes from there.
            return None
        driving = await self.coordinator.find_case_run(run_ref.case_run_id)
        if driving is not None:
            # A turn this process is driving is registered by case run before
            # its execution starts, so this covers the start window where the
            # execution id is not published yet.
            await driving.wait_ready()
            return ActiveChatRun(driving)
        if not _should_finalize_on_resume_miss(manager, run_ref):
            # A live foreign host is still starting this run and owns its
            # terminal verdict; continuing here would stack a second turn.
            raise LookupError("运行由其他进程持有，暂时无法恢复。")
        finalize_dead_run(manager, run_ref)
        return None

    async def _resolve_attached_run(
        self, context: ApiContext, request: ChatTurnRequest
    ) -> ActiveChatRun | AttachedExecutionRun | None:
        execution_id = request.execution_id
        if execution_id is None:
            raise ValueError("attached run resolution requires execution_id")
        active = await self.coordinator.find_execution(execution_id)
        if active is not None:
            await active.wait_ready()
            return ActiveChatRun(active)
        service = session_service_for_scope(context, request.scope_id)
        manager = service.manager
        run_ref = _request_case_run(manager, request)
        if run_ref is not None:
            driving = await self.coordinator.find_case_run(run_ref.case_run_id)
            if driving is not None:
                # A turn this process is already driving is registered by
                # case run before its execution starts, so this also covers
                # the start window where the execution id is not in the
                # coordinator index yet. Recovering here instead would
                # stack a second turn on the live one.
                await driving.wait_ready()
                return ActiveChatRun(driving)
        probe_lease = None
        if run_ref is None:
            probe_lease = await context.acquire_runtime(
                config=manager.config,
                manager=manager,
            )
            probe_runtime = probe_lease.runtime.runtime_service
            try:
                run_ref = await probe_runtime.recovery_case_run(execution_id)
            finally:
                await probe_lease.release()
        run_config = manager.load_run_config(
            run_ref,
            credential_source=manager.config,
        )
        lease = await context.acquire_runtime(config=run_config)
        runtime_service = lease.runtime.runtime_service
        try:
            handle = await runtime_service.runtime.get_execution_handle(
                execution_id
            )
        except KeyError:
            # A resume miss is only a death certificate for a run this
            # process could own. Hosts share one project's sessions root,
            # so the execution can also live in another live process (a
            # CLI turn the UI attached to); finalizing that run here wrote
            # a false error terminal over the owner's later real verdict.
            if not _should_finalize_on_resume_miss(manager, run_ref):
                await lease.release()
                raise LookupError("execution not found") from None
            try:
                manager.finish_case_run(
                    run_ref,
                    CaseRunStatus.ERROR,
                    error=(
                        "resume miss: this process holds no execution "
                        f"handle for {execution_id}"
                    ),
                )
            except OSError:
                # Metadata repair must not mask the resume miss; the
                # next resume attempt reconciles again.
                pass
            await lease.release()
            return None
        try:
            snapshot = await handle.snapshot()
        except BaseException:
            await lease.release()
            raise
        if snapshot.status.terminal:
            return AttachedExecutionRun(
                handle, run_ref, lease.runtime.manager, lease,
                snapshot=snapshot,
            )
        try:
            recovered = await self.coordinator.recover_turn(
                lease=lease,
                run_ref=run_ref,
                execution_id=execution_id,
            )
        except BaseException:
            await lease.release()
            raise
        await recovered.wait_ready()
        return ActiveChatRun(recovered)

    async def deliver_approval(
        self,
        context: ApiContext,
        approval_id: str,
        *,
        approved: bool,
        comment: str,
    ) -> bool:
        case_run_id = approval_id.split(":", 1)[0]
        active = await self.coordinator.find_case_run(case_run_id)
        if active is not None:
            return await active.runtime_service.deliver_approval(
                approval_id,
                approved=approved,
                comment=comment,
            )
        lease = await context.acquire_runtime()
        try:
            return await lease.runtime.runtime_service.deliver_approval(
                approval_id,
                approved=approved,
                comment=comment,
            )
        finally:
            await lease.release()

    async def deliver_steering(
        self, execution_id: str, *, session_id: str, input_id: str,
        message: str | None, attachments: Sequence[str] = (),
    ) -> Any:
        active = await self.coordinator.find_execution(execution_id)
        if active is None or active.run_ref is None or active.run_ref.session_id != session_id:
            raise LookupError("当前会话没有对应的运行中执行，请等待连接恢复后重试。")
        if attachments:
            # Fail before the input is queued: send_steering resolves again
            # against the same workspace root.
            resolve_attachments(
                attachments, active.runtime_service.config.workspace_root
            )
        if realtime_enabled_for(active.manager.load(session_id)):
            # Realtime mode: a mid-turn message forks a speak reply instead of
            # steering the running execution. The reply rides the HTTP
            # response — it is transient by design and never enters the
            # session transcript.
            if attachments:
                raise ValueError("实时插话暂不支持附件，请先移除附件后重试。")
            outcome = await active.runtime_service.send_interrupt(
                execution_id, manager=active.manager, run_ref=active.run_ref,
                session_id=session_id, input_id=input_id, message=message or "",
            )
            if outcome.speak_args is not None:
                reply = {
                    "via": "speak",
                    "text": outcome.speak_args.text,
                    "attachments": list(outcome.speak_args.attachments),
                    "say_nothing": outcome.speak_args.say_nothing,
                }
            else:
                reply = {
                    "via": "text",
                    "text": outcome.text,
                    "attachments": [],
                    "say_nothing": False,
                }
            return ForkReplyReceipt(input_id=input_id, reply=reply)
        return await active.runtime_service.send_steering(
            execution_id, input_id=input_id, message=message,
            case_run_id=active.run_ref.case_run_id,
            attachments=attachments,
        )

    async def cancel_turn(
        self, context: ApiContext, execution_id: str, *, session_id: str,
    ) -> bool:
        active = await self.coordinator.find_execution(execution_id)
        if active is not None:
            if active.run_ref is None or active.run_ref.session_id != session_id:
                raise LookupError("当前会话没有对应的运行中执行，请等待连接恢复后重试。")
            return await active.runtime_service.cancel_turn(execution_id)
        lease = await context.acquire_runtime()
        try:
            return await lease.runtime.runtime_service.cancel_turn(execution_id)
        except KeyError as exc:
            raise LookupError(f"未知执行：{execution_id}") from exc
        finally:
            await lease.release()

    async def close(self) -> None:
        await self.coordinator.close()


def _request_case_run(manager: Any, request: ChatTurnRequest) -> CaseRunRef | None:
    if not request.session_id or not request.execution_id:
        return None
    metadata = manager.show(request.session_id)["metadata"]
    case_run_id = metadata.get("last_case_run_id")
    if not isinstance(case_run_id, str) or not case_run_id:
        return None
    run_ref = manager.find_case_run(request.session_id, case_run_id)
    run_metadata = read_json(Path(run_ref.run_dir) / "run_metadata.json")
    return (
        run_ref
        if run_metadata.get("runtime_execution_id") == request.execution_id
        else None
    )


def _execution_event(raw: Any) -> ExecutionEvent:
    if isinstance(raw, dict):
        value = thaw_json(raw)
    else:
        value = {
            "schema_version": raw.schema_version,
            "event_id": raw.event_id,
            "execution_id": raw.execution_id,
            "attempt_id": raw.attempt_id,
            "trace_id": raw.trace_id,
            "span_id": raw.span_id,
            "parent_span_id": raw.parent_span_id,
            "sequence": raw.sequence,
            "timestamp_unix_ns": raw.timestamp_unix_ns,
            "kind": raw.kind,
            "module_path": raw.module_path,
            "data": thaw_json(raw.data),
        }
    return ExecutionEvent.model_validate(value)


def _keepalive_event(execution_id: str, sequence: int) -> ExecutionEvent:
    timestamp = time.time_ns()
    return ExecutionEvent(
        schema_version="1",
        event_id=f"lara-transport-keepalive-{timestamp}",
        execution_id=execution_id,
        attempt_id="transport",
        trace_id="transport",
        span_id="transport",
        sequence=sequence,
        timestamp_unix_ns=timestamp,
        module_path="lara.transport",
        kind="lara.transport.keepalive",
    )


_STATUS_TO_TERMINAL_EVENT = {
    "succeeded": "execution.completed",
    "failed": "execution.failed",
    "cancelled": "execution.cancelled",
    "deadline_exceeded": "execution.deadline_exceeded",
}


def _terminal_event_from_snapshot(snapshot: Any) -> ExecutionEvent:
    """Build a terminal SSE event from a Pygent ExecutionSnapshot.

    Used when subscribing to a finished execution whose in-memory event buffer
    has already drained, so the subscriber yields no terminal event.
    """
    kind = _STATUS_TO_TERMINAL_EVENT.get(
        getattr(snapshot.status, "value", ""), "execution.failed"
    )
    timestamp = time.time_ns()
    return ExecutionEvent(
        schema_version="1",
        event_id=f"{kind}-{timestamp}",
        execution_id=snapshot.execution_id,
        attempt_id=snapshot.attempt_id or "recovery",
        trace_id=snapshot.trace_id or "recovery",
        span_id="recovery",
        sequence=snapshot.terminal_sequence
        if snapshot.terminal_sequence is not None
        else snapshot.last_sequence,
        timestamp_unix_ns=timestamp,
        module_path="lara.api",
        kind=kind,
        data={},
    )


def _transport_error_event(execution_id: str, error: BaseException) -> ExecutionEvent:
    return ExecutionEvent(
        schema_version="1",
        event_id="lara-transport-error",
        execution_id=execution_id,
        attempt_id="transport",
        trace_id="transport",
        span_id="transport",
        sequence=0,
        timestamp_unix_ns=time.time_ns(),
        module_path="lara.transport",
        kind="lara.transport.error",
        data={"error": str(error), "error_type": type(error).__name__},
    )


def _append_model_text_delta(event: ExecutionEvent, *, stream_dir: Path) -> None:
    if event.kind != "model.text.delta":
        return
    text = event.data.get("text")
    if not isinstance(text, str) or not text:
        return
    append_jsonl(
        stream_dir / "streams" / "model_text_deltas.jsonl",
        {
            "execution_id": event.execution_id,
            "sequence": event.sequence,
            "timestamp_unix_ns": event.timestamp_unix_ns,
            "module_path": event.module_path,
            "text": text,
        },
    )


def _sse(event: ExecutionEvent) -> str:
    if event.kind == "lara.transport.keepalive":
        return ": keep-alive\n\n"
    return f"event: execution.event\ndata: {json.dumps(event.model_dump(), ensure_ascii=False, sort_keys=True)}\n\n"
