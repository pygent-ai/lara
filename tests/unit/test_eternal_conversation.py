import asyncio
import json
import subprocess
import sys
import uuid
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace

import pytest
from pygent import AIMessage, Context, Message, Module, UserMessage
from pygent.tool import StandardTools
from pygent.runtime.codec import message_to_dict

from tests.unit.test_model_configuration import native_runtime_config
from lara.runtime.eternal_conversation import (
    BUILDER_GUIDANCE,
    EXTRACTOR_GUIDANCE,
    MAX_MEMORY_JOB_ATTEMPTS,
    MEMORY_JOB_FALLBACK_FOREGROUND_PROMPT,
    DynamicMemoryCli,
    EternalConversationHarness,
    MemoryJobContext,
    MemoryJobRequest,
    estimate_context_tokens,
    load_projection,
    render_memory_context,
)
from lara.runtime.service import LaraRuntimeService
from lara.schema import EternalConversationConfig, RunConfig
from lara.sessions import SessionManager

SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "memory-cli"
    / "skills"
    / "dynamic-memory-cli"
    / "scripts"
    / "dynamic_memory_cli.py"
)


def _memory_cli_for(request: MemoryJobRequest) -> DynamicMemoryCli:
    return DynamicMemoryCli(SCRIPT, request.context.memory_root)


async def _publish_via_cli(
    request: MemoryJobRequest,
    *,
    snapshot: dict,
    changed_uts: list[dict],
    semantic_statement: str = "The range is represented.",
) -> None:
    """Simulate the fork agent publishing its proposal through the CLI."""

    cli = _memory_cli_for(request)
    state = await cli.call("get-state")
    jobs = request.context.memory_root / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    proposal = {
        "base_memory_revision": state["memory_revision"],
        "base_snapshot_revision": state["snapshot_revision"],
        "from_cursor": request.context.covered_through + 1,
        "to_cursor": request.context.target_cursor,
        "snapshot": snapshot,
        "changed_uts": changed_uts,
        "evidence_refs": [request.context.evidence_ref],
        "semantic_statement": semantic_statement,
    }
    for change in changed_uts:
        if change.get("action", "upsert") == "upsert":
            change.setdefault("source", request.context.session_id)
            change.setdefault("evidence_refs", [request.context.evidence_ref])
    path = (
        jobs
        / f"proposal-{request.context.covered_through + 1}-{request.context.target_cursor}.json"
    )
    path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
    await cli.file_command("publish-pending", proposal, path.name)


class _SeamScriptedState:
    __slots__ = ("seen",)

    def __init__(self) -> None:
        self.seen: list[tuple[Message, Context]] = []


class _SeamScriptedModel(Module[Message, AIMessage]):
    """Scripted model for the memory-job seam test; records what it sees."""

    trusted_live_resource_attributes = ("state",)

    def __init__(self) -> None:
        super().__init__()
        self.state = _SeamScriptedState()

    async def forward(
        self, message: Message, context: Context
    ) -> tuple[AIMessage, Context]:
        self.state.seen.append((message, context))
        return AIMessage(content="job finished"), context


class _StubMemoryLaraAgent:
    """Stands in for the child LaraAgent; only llm/_model_entries/aclose used."""

    def __init__(self) -> None:
        self.llm = object()

    def _model_entries(self) -> tuple:
        return ()

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_memory_job_runner_forks_extractor_and_runs_builder_as_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = native_runtime_config(tmp_path)
    service = object.__new__(LaraRuntimeService)
    service.config = config
    service._memory_job_tools = StandardTools(
        workspace_root=str(tmp_path), restrict_to_workspace=False
    )
    runs: list[tuple[tuple, _SeamScriptedModel]] = []

    def model_factory(definitions: tuple) -> _SeamScriptedModel:
        model = _SeamScriptedModel()
        runs.append((definitions, model))
        return model

    monkeypatch.setattr(
        "lara.runtime.service.build_model_layer_factory",
        lambda *_args, **_kwargs: model_factory,
    )
    monkeypatch.setattr(
        "lara.runtime.service.LaraAgent",
        lambda *_args, **_kwargs: _StubMemoryLaraAgent(),
    )
    monkeypatch.setattr(
        service,
        "_child_agent_config",
        lambda alias: SimpleNamespace(
            resolved_agent=SimpleNamespace(default_model_group="memory-group")
        ),
    )

    history = [
        message_to_dict(UserMessage(content="remember the exact constraint")),
        message_to_dict(AIMessage(content="recorded")),
    ]
    extractor = MemoryJobRequest(
        role="extractor",
        alias="extractor",
        label="lara-fork.extractor:1-2",
        system_prompt="foreground prompt",
        history_range=tuple(history),
        directive="extract directive",
        context=MemoryJobContext(
            session_id="session-1",
            memory_script=SCRIPT,
            memory_root=tmp_path / "memory",
            evidence_ref="raw-history:range-1-2",
            covered_through=0,
            target_cursor=2,
        ),
    )

    text = await service._run_memory_job(extractor)

    assert text == "job finished"
    definitions, model = runs[-1]
    names = {definition.name for definition in definitions}
    # aligned with the foreground StandardTools composition
    assert {"bash", "read", "write", "edit", "glob", "grep"} <= names
    assert "memory_cli" not in names
    seen_message, seen_context = model.state.seen[0]
    # block 1: verbatim foreground prompt + uncompacted history
    assert seen_context.system_prompt == "foreground prompt"
    assert [entry.content for entry in seen_context.messages] == [
        "remember the exact constraint",
        "recorded",
    ]  # verbatim, uncompacted (wire dicts rebuilt into native messages)
    assert seen_message.content == "extract directive"

    builder = MemoryJobRequest(
        role="builder",
        alias="builder",
        label="lara-child.builder:1",
        system_prompt=BUILDER_GUIDANCE,
        history_range=(),
        directive="build directive",
        context=MemoryJobContext(
            session_id="session-1",
            memory_script=SCRIPT,
            memory_root=tmp_path / "memory",
            evidence_ref="build-batch:x",
            covered_through=0,
            target_cursor=0,
            build_batch_path=tmp_path / "batch.json",
        ),
    )

    await service._run_memory_job(builder)

    _, builder_model = runs[-1]
    builder_message, builder_context = builder_model.state.seen[0]
    # the builder is an independent child: own prompt, fresh context
    assert builder_context.system_prompt == BUILDER_GUIDANCE
    assert builder_context.messages == ()
    assert builder_message.content == "build directive"


@pytest.mark.asyncio
async def test_wait_idle_isolates_failed_background_job_from_foreground() -> None:
    async def fail() -> None:
        raise ValueError("bad extractor output")

    harness = EternalConversationHarness(
        EternalConversationConfig(enabled=True),
        run_memory_job=lambda *_args: asyncio.sleep(0, result="done"),
    )
    harness._workers["session-1"] = asyncio.create_task(fail())

    await harness.wait_idle()
    assert harness._workers == {}


@pytest.mark.asyncio
async def test_retry_pending_restarts_only_the_failed_memory_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = RunConfig(workspace_root=str(tmp_path), lara_root=str(tmp_path / ".lara"))
    manager = SessionManager(config)
    ref = manager.create("retry-memory", mode="chat")
    session = manager.load(ref.session_id)
    session_dir = Path(session.session_dir)
    (session_dir / "state").mkdir(parents=True, exist_ok=True)
    (session_dir / "state" / "eternal-harness.json").write_text(
        json.dumps({"requested_cursor": 5}), encoding="utf-8"
    )
    (session_dir / "state" / "eternal-conversation.json").write_text(
        json.dumps({"covered_through": 2}), encoding="utf-8"
    )
    harness = EternalConversationHarness(
        EternalConversationConfig(enabled=True),
        run_memory_job=lambda *_args: asyncio.sleep(0, result="done"),
    )
    resumed: list[str] = []

    async def resumed_job(session_id: str, _session_dir: Path) -> None:
        resumed.append(session_id)

    monkeypatch.setattr(harness, "_guarded_extract_loop", resumed_job)

    assert await harness.retry_pending(session) is True
    assert await harness.retry_pending(session) is False
    await harness.wait_idle()

    assert resumed == [session.session_id]


def test_eternal_trigger_config_validation() -> None:
    assert EternalConversationConfig(enabled=True).extraction_trigger == "turn"
    assert (
        EternalConversationConfig(
            enabled=True,
            extraction_trigger="context_ratio",
            extraction_trigger_ratio=0.7,
        ).extraction_trigger_ratio
        == 0.7
    )
    with pytest.raises(ValueError, match="extraction_trigger must be"):
        EternalConversationConfig(enabled=True, extraction_trigger="every_turn")
    with pytest.raises(ValueError, match="extraction_trigger_ratio must be"):
        EternalConversationConfig(enabled=True, extraction_trigger_ratio=1.0)
    with pytest.raises(ValueError, match="extraction_trigger_ratio must be"):
        EternalConversationConfig(enabled=True, extraction_trigger_ratio=0)


def test_estimate_context_tokens_counts_snapshot_plus_uncovered_tail() -> None:
    history = [
        {"role": "user", "content": "old " * 200},
        {"role": "assistant", "content": "tail"},
    ]
    covered_all = estimate_context_tokens(
        history, {"covered_through": 2, "snapshot": {}}
    )
    partially_covered = estimate_context_tokens(
        history, {"covered_through": 1, "snapshot": {}}
    )
    uncovered = estimate_context_tokens(history, {})

    assert covered_all < partially_covered < uncovered


@pytest.mark.asyncio
async def test_context_ratio_trigger_defers_extraction_below_threshold(
    tmp_path: Path,
) -> None:
    config = RunConfig(workspace_root=str(tmp_path), lara_root=str(tmp_path / ".lara"))
    manager = SessionManager(config)
    ref = manager.create("chat", mode="chat")
    session = manager.load(ref.session_id)
    session.history = [{"role": "user", "content": "short"}]
    manager.save(session)
    harness = EternalConversationHarness(
        EternalConversationConfig(
            enabled=True,
            extraction_trigger="context_ratio",
            extraction_trigger_ratio=0.7,
        ),
        run_memory_job=lambda *_args: asyncio.sleep(0, result="done"),
    )

    await harness.record_and_trigger(session, context_window_tokens=1_000_000)
    await harness.wait_idle()

    state = json.loads(
        (Path(ref.session_dir) / "state" / "eternal-harness.json").read_text(
            encoding="utf-8"
        )
    )
    # Recording keeps the observability contract; only extraction defers.
    assert state["recorded_messages"] == 1
    assert "requested_cursor" not in state
    assert "extractor" not in (state.get("memory_jobs") or {})
    raw = (Path(ref.session_dir) / "raw-history" / "events.jsonl").read_text(
        encoding="utf-8"
    )
    assert "short" in raw


@pytest.mark.asyncio
async def test_context_ratio_trigger_spawns_extraction_at_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = RunConfig(workspace_root=str(tmp_path), lara_root=str(tmp_path / ".lara"))
    manager = SessionManager(config)
    ref = manager.create("chat", mode="chat")
    session = manager.load(ref.session_id)
    session.history = [{"role": "user", "content": "x" * 4_000}]
    manager.save(session)
    harness = EternalConversationHarness(
        EternalConversationConfig(
            enabled=True,
            extraction_trigger="context_ratio",
            extraction_trigger_ratio=0.7,
        ),
        run_memory_job=lambda *_args: asyncio.sleep(0, result="done"),
    )
    started: list[str] = []

    async def extract(session_id: str, _session_dir: Path) -> None:
        started.append(session_id)

    monkeypatch.setattr(harness, "_guarded_extract_loop", extract)

    await harness.record_and_trigger(session, context_window_tokens=1_000)
    await harness.wait_idle()

    assert started == [ref.session_id]
    state = json.loads(
        (Path(ref.session_dir) / "state" / "eternal-harness.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["requested_cursor"] == 1
    assert state["memory_jobs"]["extractor"]["status"] == "queued"


@pytest.mark.asyncio
async def test_context_ratio_trigger_falls_back_without_window_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = RunConfig(workspace_root=str(tmp_path), lara_root=str(tmp_path / ".lara"))
    manager = SessionManager(config)
    ref = manager.create("chat", mode="chat")
    session = manager.load(ref.session_id)
    session.history = [{"role": "user", "content": "short"}]
    manager.save(session)
    harness = EternalConversationHarness(
        EternalConversationConfig(
            enabled=True,
            extraction_trigger="context_ratio",
            extraction_trigger_ratio=0.7,
        ),
        run_memory_job=lambda *_args: asyncio.sleep(0, result="done"),
    )
    started: list[str] = []

    async def extract(session_id: str, _session_dir: Path) -> None:
        started.append(session_id)

    monkeypatch.setattr(harness, "_guarded_extract_loop", extract)

    await harness.record_and_trigger(session, context_window_tokens=None)
    await harness.wait_idle()

    assert started == [ref.session_id]


@pytest.mark.asyncio
async def test_turn_trigger_extraction_ignores_window_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = RunConfig(workspace_root=str(tmp_path), lara_root=str(tmp_path / ".lara"))
    manager = SessionManager(config)
    ref = manager.create("chat", mode="chat")
    session = manager.load(ref.session_id)
    session.history = [{"role": "user", "content": "short"}]
    manager.save(session)
    harness = EternalConversationHarness(
        EternalConversationConfig(enabled=True),
        run_memory_job=lambda *_args: asyncio.sleep(0, result="done"),
    )
    started: list[str] = []

    async def extract(session_id: str, _session_dir: Path) -> None:
        started.append(session_id)

    monkeypatch.setattr(harness, "_guarded_extract_loop", extract)

    await harness.record_and_trigger(session, context_window_tokens=1_000_000)
    await harness.wait_idle()

    assert started == [ref.session_id]
    state = json.loads(
        (Path(ref.session_dir) / "state" / "eternal-harness.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["requested_cursor"] == 1


@pytest.mark.asyncio
async def test_memory_workers_do_not_inherit_foreground_execution_context(
    tmp_path: Path,
) -> None:
    foreground_scope: ContextVar[str | None] = ContextVar(
        "test_foreground_scope", default=None
    )
    observed = []
    config = RunConfig(workspace_root=str(tmp_path), lara_root=str(tmp_path / ".lara"))
    manager = SessionManager(config)
    ref = manager.create("chat", mode="chat")
    session = manager.load(ref.session_id)
    session.history = [{"role": "user", "content": "remember this"}]
    manager.save(session)

    async def run_memory_job(request: MemoryJobRequest) -> str:
        observed.append(foreground_scope.get())
        cli = DynamicMemoryCli(SCRIPT, request.context.memory_root)
        if request.role == "extractor":
            await _publish_via_cli(
                request,
                snapshot={
                    "resident_memory": [],
                    "recent_context": [],
                    "current_state": [],
                    "completed": [],
                    "next_actions": [],
                    "constraints": [],
                },
                changed_uts=[
                    {
                        "action": "upsert",
                        "id": "ut-context",
                        "memory_id": "memory-context",
                        "priority": 50,
                        "content": "Remember this context boundary.",
                        "queries": ["context boundary"],
                        "must_include": ["context boundary"],
                        "tags": ["test"],
                    }
                ],
            )
        else:
            await cli.call(
                "build-pending", "--file", str(request.context.build_batch_path)
            )
        return "done"

    harness = EternalConversationHarness(
        EternalConversationConfig(enabled=True, dynamic_memory_cli_path=str(SCRIPT)),
        run_memory_job=run_memory_job,
    )
    token = foreground_scope.set("active-foreground-execution")
    try:
        await harness.record_and_trigger(manager.load(ref.session_id))
        await harness.wait_idle()
    finally:
        foreground_scope.reset(token)

    assert observed == [None, None]


def _cli_publish_error(tmp_path: Path, proposal: dict) -> str:
    root = tmp_path / f"memory-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "init", "--path", str(root)],
        check=True,
        capture_output=True,
    )
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(root),
            "publish-pending",
            "--file",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    return proc.stdout + proc.stderr


def _valid_shape_proposal(**overrides: object) -> dict:
    proposal: dict = {
        "base_memory_revision": 0,
        "base_snapshot_revision": 0,
        "from_cursor": 1,
        "to_cursor": 2,
        "snapshot": {
            "resident_memory": [],
            "recent_context": [],
            "current_state": [],
            "completed": [],
            "next_actions": [],
            "constraints": [],
        },
        "changed_uts": [],
        "evidence_refs": ["raw-history:range-1-2"],
        "semantic_statement": "represented",
    }
    proposal.update(overrides)
    return proposal


def test_cli_publish_enforces_compact_snapshot_and_ut_limits(tmp_path: Path) -> None:
    snapshot = _valid_shape_proposal()["snapshot"]
    snapshot["resident_memory"] = ["x"] * 5
    error = _cli_publish_error(tmp_path, _valid_shape_proposal(snapshot=snapshot))
    assert "has 5 items; compact output limit is 4" in error

    error = _cli_publish_error(tmp_path, _valid_shape_proposal(changed_uts=[{}] * 9))
    assert "at most 8 items" in error


def test_cli_publish_rejects_update_action_with_upsert_guidance(tmp_path: Path) -> None:
    error = _cli_publish_error(
        tmp_path,
        _valid_shape_proposal(changed_uts=[{"action": "update", "id": "existing-ut"}]),
    )
    assert "unsupported action 'update'" in error
    assert "use action='upsert' to create or update a UT" in error


def test_cli_publish_is_not_silently_truncated(tmp_path: Path) -> None:
    snapshot = _valid_shape_proposal()["snapshot"]
    snapshot["resident_memory"] = ["x" * 501] * 4
    error = _cli_publish_error(tmp_path, _valid_shape_proposal(snapshot=snapshot))
    assert "snapshot.resident_memory[0] has 501 characters" in error
    assert "split detailed facts" in error

    oversized_ut = {
        "action": "upsert",
        "id": "too-long",
        "memory_id": "memory-too-long",
        "priority": 50,
        "content": "c" * 701,
        "queries": ["q"] * 6,
        "must_include": ["c"] * 5,
        "source": "session",
        "evidence_refs": ["raw-history:range-1-2"],
    }
    error = _cli_publish_error(
        tmp_path, _valid_shape_proposal(changed_uts=[oversized_ut])
    )
    assert "content has 701 characters" in error
    assert "split its details" in error


def test_memory_context_requires_clarification_for_unacknowledged_conflicts(
    tmp_path: Path,
) -> None:
    prompt = render_memory_context(
        tmp_path,
        {
            "snapshot": {},
            "covered_through": 4,
            "memory_cli_command": '"python" "/memory/dynamic_memory_cli.py" --root "/session/memory"',
        },
    )
    assert "an override is acknowledged only when" in prompt
    assert (
        "states only the new, contradictory behavior is always unacknowledged" in prompt
    )
    assert "search both dynamic memory and Raw History" in prompt
    assert "earliest relevant direct-user matches" in prompt
    assert "tail-only view" in prompt
    assert "Raw History is the required fallback" in prompt
    assert "support-window, version, release, compatibility, and deprecation" in prompt
    assert "Do not accept the new request, reject it" in prompt
    assert "Stop further exploration immediately" in prompt
    assert "at most 120 words" in prompt
    assert "end the response with exactly one direct clarification question" in prompt


def test_memory_context_requires_memory_search_evidence_for_conflicts(
    tmp_path: Path,
) -> None:
    prompt = render_memory_context(
        tmp_path,
        {
            "snapshot": {},
            "covered_through": 4,
            "memory_cli_command": '"python" "/memory/dynamic_memory_cli.py" --root "/session/memory"',
        },
    )
    assert "search dynamic memory at least twice" in prompt
    assert (
        "once for the exact proper noun, codename, alias, or named constraint"
        in prompt
    )
    assert (
        "once for the affected component or the contradictory behavior" in prompt
    )
    assert (
        "the only permitted tool calls are the required memory searches" in prompt
    )
    assert "do not inspect, modify, or test project files" in prompt
    assert "repeat that exact original term" in prompt
    assert "never replace it with a generic label" in prompt
    assert "the final character of your response MUST be" in prompt


def test_memory_context_conflict_evidence_falls_back_to_raw_history(
    tmp_path: Path,
) -> None:
    prompt = render_memory_context(tmp_path, {"snapshot": {}, "covered_through": 4})
    assert "perform the same two lookups directly against Raw History" in prompt
    assert "the only permitted tool calls are the required memory searches" in prompt
    assert "repeat that exact original term" in prompt


def test_memory_context_explains_automatic_journaling(tmp_path: Path) -> None:
    prompt = render_memory_context(tmp_path, {"snapshot": {}, "covered_through": 4})
    assert "Memory publication is automatic and asynchronous" in prompt
    assert "you may promise the user that something will be remembered" in prompt
    assert "do not claim loss" in prompt
    assert "long-term publication is pending" in prompt


@pytest.mark.parametrize(
    "projection",
    [
        {},
        {"memory_cli_command": ""},
        {"memory_cli_command": "  "},
        {"snapshot": {"constraints": ["Keep prior work"]}},
    ],
)
def test_unpublished_memory_does_not_advertise_an_uninstalled_command(
    tmp_path: Path, projection: dict
) -> None:
    prompt = render_memory_context(tmp_path, projection)
    assert "Memory search is not available" in prompt
    assert "Do not invoke or invent a memory CLI command" in prompt
    assert "Search command:" not in prompt
    assert "dynamic-memory-cli" not in prompt
    assert "search both dynamic memory" not in prompt
    assert "Raw History" in prompt
    assert str(tmp_path / "raw-history" / "events.jsonl") in prompt


def test_published_memory_uses_the_explicit_command(tmp_path: Path) -> None:
    command = '"C:/Python/python.exe" "C:/Memory Tools/dynamic_memory_cli.py" --root "C:/Session/memory"'
    prompt = render_memory_context(tmp_path, {"memory_cli_command": command})
    assert f"Search command: {command} search <keyword-or-key-phrase>" in prompt
    assert "Memory search is not available" not in prompt


def test_extractor_retains_private_compatibility_boundaries() -> None:
    assert "Version support windows" in EXTRACTOR_GUIDANCE
    assert "exact version/date boundary" in EXTRACTOR_GUIDANCE
    assert "own stable UT" in EXTRACTOR_GUIDANCE
    assert "MUST NOT share a UT" in EXTRACTOR_GUIDANCE
    assert "carry forward every still-effective decision" in EXTRACTOR_GUIDANCE
    assert "absence from the frozen Working" in EXTRACTOR_GUIDANCE
    assert "Memory is not evidence" in EXTRACTOR_GUIDANCE


def test_extractor_retains_named_entities_as_retrievable_memory() -> None:
    assert "user-defined proper" in EXTRACTOR_GUIDANCE
    assert "internal codenames" in EXTRACTOR_GUIDANCE
    assert "environment names" in EXTRACTOR_GUIDANCE
    assert "exact user-authored name" in EXTRACTOR_GUIDANCE
    assert "at least one query, and must_include" in EXTRACTOR_GUIDANCE
    assert "never leave it only in the Snapshot" in EXTRACTOR_GUIDANCE
    assert "memory-only visibility, not" in EXTRACTOR_GUIDANCE
    assert "self-audit every future-relevant named entity" in EXTRACTOR_GUIDANCE


@pytest.mark.asyncio
async def test_harness_records_raw_history_publishes_snapshot_and_builds_memory(
    tmp_path: Path,
) -> None:
    config = RunConfig(workspace_root=str(tmp_path), lara_root=str(tmp_path / ".lara"))
    manager = SessionManager(config)
    ref = manager.create("chat", mode="chat")
    session = manager.load(ref.session_id)
    session.history = [
        {"role": "user", "content": "Use blue deployments for this service."},
        {
            "role": "assistant",
            "content": "I will preserve blue deployments as a constraint.",
        },
    ]
    manager.save(session)

    requests: list[MemoryJobRequest] = []

    async def run_memory_job(request: MemoryJobRequest) -> str:
        requests.append(request)
        if request.role == "extractor":
            # the fork agent publishes through the memory CLI itself
            await _publish_via_cli(
                request,
                snapshot={
                    "resident_memory": ["Use blue deployments"],
                    "recent_context": [],
                    "current_state": [],
                    "completed": [],
                    "next_actions": [],
                    "constraints": ["Use blue deployments"],
                },
                changed_uts=[
                    {
                        "action": "upsert",
                        "id": "ut-blue",
                        "memory_id": "memory-blue",
                        "priority": 80,
                        "content": "The service must use blue deployments.",
                        "queries": ["service deployment", "deployment constraint"],
                        "must_include": ["blue deployments"],
                        "tags": ["constraint"],
                    }
                ],
                semantic_statement="The deployment constraint is carried.",
            )
        else:
            cli = _memory_cli_for(request)
            await cli.call(
                "build-pending", "--file", str(request.context.build_batch_path)
            )
        return "done"

    harness = EternalConversationHarness(
        EternalConversationConfig(
            enabled=True,
            extractor_agent_alias="extractor",
            builder_agent_alias="builder",
            dynamic_memory_cli_path=str(SCRIPT),
        ),
        run_memory_job=run_memory_job,
    )
    await harness.record_and_trigger(manager.load(ref.session_id))
    await harness.wait_idle()

    projection = load_projection(ref.session_dir)
    assert projection["covered_through"] == 2
    assert projection["snapshot"]["constraints"] == ["Use blue deployments"]
    assert "--root" in projection["memory_cli_command"]
    raw = (Path(ref.session_dir) / "raw-history" / "events.jsonl").read_text(
        encoding="utf-8"
    )
    assert "Use blue deployments" in raw
    # fork shape: block 1 is the foreground system prompt plus the verbatim
    # uncompacted history; block 2 is the <system-reminder> directive
    extractor_request = requests[0]
    assert extractor_request.role == "extractor"
    # legacy sessions with no stored prompt get the explicit fallback
    assert extractor_request.system_prompt == MEMORY_JOB_FALLBACK_FOREGROUND_PROMPT
    assert [entry["content"] for entry in extractor_request.history_range] == [
        "Use blue deployments for this service.",
        "I will preserve blue deployments as a constraint.",
    ]
    assert extractor_request.directive.startswith("<system-reminder>")
    assert "publish-pending" in extractor_request.directive
    assert "Old Snapshot" in extractor_request.directive
    assert requests[0].label != requests[1].label
    extractor_history = (
        Path(ref.session_dir) / "agent-history" / "extractor" / "conversation.jsonl"
    ).read_text(encoding="utf-8")
    # the directive is logged content-addressed only: no snapshot/UT text
    assert '"directive_hash"' in extractor_history
    assert '"directive"' not in extractor_history
    assert "Use blue deployments for this service." not in extractor_history
    assert (
        Path(ref.session_dir) / "agent-history" / "builder" / "conversation.jsonl"
    ).exists()
    assert [request.role for request in requests] == ["extractor", "builder"]

    import sqlite3

    with sqlite3.connect(
        Path(ref.session_dir) / "memory" / "memory.sqlite3"
    ) as connection:
        assert (
            connection.execute(
                "SELECT build_state FROM uts WHERE id='ut-blue'"
            ).fetchone()[0]
            == "built"
        )
    listed = await DynamicMemoryCli(SCRIPT, Path(ref.session_dir) / "memory").call(
        "list", "--full"
    )
    assert any("blue deployments" in item["content"] for item in listed["memories"])


@pytest.mark.asyncio
async def test_harness_retries_invalid_proposal_and_only_freezes_uncovered_history(
    tmp_path: Path,
) -> None:
    config = RunConfig(workspace_root=str(tmp_path), lara_root=str(tmp_path / ".lara"))
    manager = SessionManager(config)
    ref = manager.create("chat", mode="chat")
    requests: list[MemoryJobRequest] = []
    invalid_once = True
    rejected_inside_fork = 0

    async def run_memory_job(request: MemoryJobRequest) -> str:
        nonlocal invalid_once, rejected_inside_fork
        requests.append(request)
        if request.role == "extractor":
            if invalid_once:
                invalid_once = False
                # the fork self-corrects inside one ReAct execution: the CLI
                # rejects the oversized UT, the agent shortens and re-publishes
                # CLI errors surface as RuntimeError carrying the JSON message
                with pytest.raises(RuntimeError, match="content has 701 characters"):
                    await _publish_via_cli(
                        request,
                        snapshot={
                            "resident_memory": [],
                            "recent_context": [],
                            "current_state": [],
                            "completed": [],
                            "next_actions": [],
                            "constraints": [],
                        },
                        changed_uts=[
                            {
                                "action": "upsert",
                                "id": "too-long",
                                "memory_id": "memory-too-long",
                                "priority": 50,
                                "content": "x" * 701,
                                "queries": [],
                                "must_include": [],
                            }
                        ],
                    )
                rejected_inside_fork += 1
            await _publish_via_cli(
                request,
                snapshot={
                    "resident_memory": [],
                    "recent_context": [],
                    "current_state": [],
                    "completed": [],
                    "next_actions": [],
                    "constraints": [],
                },
                changed_uts=[],
                semantic_statement="The new range is represented by the snapshot.",
            )
        return "done"

    harness = EternalConversationHarness(
        EternalConversationConfig(enabled=True, dynamic_memory_cli_path=str(SCRIPT)),
        run_memory_job=run_memory_job,
    )
    session = manager.load(ref.session_id)
    session.history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "one"},
    ]
    manager.save(session)
    await harness.record_and_trigger(
        session, model_envelope={"system_prompt": "host", "tools": []}
    )
    await harness.wait_idle()

    session = manager.load(ref.session_id)
    session.history.extend(
        [{"role": "user", "content": "second"}, {"role": "assistant", "content": "two"}]
    )
    manager.save(session)
    await harness.record_and_trigger(session)
    await harness.wait_idle()

    extractor_requests = [
        request for request in requests if request.role == "extractor"
    ]
    assert len(extractor_requests) == 2  # one fork execution per frozen range
    assert rejected_inside_fork == 1
    # only the uncovered suffix is frozen into the second fork, verbatim
    assert [entry["content"] for entry in extractor_requests[-1].history_range] == [
        "second",
        "two",
    ]
    assert load_projection(ref.session_dir)["covered_through"] == 4
    extractor_history = (
        Path(ref.session_dir) / "agent-history" / "extractor" / "conversation.jsonl"
    ).read_text(encoding="utf-8")
    assert (
        "extract directive" not in extractor_history
    )  # directives are in fork block 2
    foreground_history = (
        Path(ref.session_dir) / "agent-history" / "foreground" / "conversation.jsonl"
    ).read_text(encoding="utf-8")
    assert "first" in foreground_history and "second" in foreground_history
    raw_history = (Path(ref.session_dir) / "raw-history" / "events.jsonl").read_text(
        encoding="utf-8"
    )
    assert "model-visible-envelope" in raw_history and "system_prompt" in raw_history


@pytest.mark.asyncio
async def test_failed_builder_keeps_pending_ut_and_recovers_without_rolling_back_turn(
    tmp_path: Path,
) -> None:
    config = RunConfig(workspace_root=str(tmp_path), lara_root=str(tmp_path / ".lara"))
    manager = SessionManager(config)
    ref = manager.create("chat", mode="chat")
    session = manager.load(ref.session_id)
    session.history = [
        {"role": "user", "content": "Remember the canary release decision."},
        {"role": "assistant", "content": "The canary decision is recorded."},
    ]
    manager.save(session)
    builder_failures = MAX_MEMORY_JOB_ATTEMPTS

    async def run_memory_job(request: MemoryJobRequest) -> str:
        nonlocal builder_failures
        if request.role == "extractor":
            await _publish_via_cli(
                request,
                snapshot={
                    "resident_memory": ["Use canary releases"],
                    "recent_context": [],
                    "current_state": [],
                    "completed": [],
                    "next_actions": [],
                    "constraints": ["Use canary releases"],
                },
                changed_uts=[
                    {
                        "action": "upsert",
                        "id": "ut-canary",
                        "memory_id": "memory-canary",
                        "priority": 80,
                        "content": "The service must use canary releases.",
                        "queries": ["release strategy"],
                        "must_include": ["canary releases"],
                        "tags": ["decision"],
                    }
                ],
                semantic_statement="The release decision is carried.",
            )
        elif builder_failures:
            builder_failures -= 1
            raise RuntimeError("simulated background builder outage")
        else:
            cli = _memory_cli_for(request)
            await cli.call(
                "build-pending", "--file", str(request.context.build_batch_path)
            )
        return "done"

    harness = EternalConversationHarness(
        EternalConversationConfig(enabled=True, dynamic_memory_cli_path=str(SCRIPT)),
        run_memory_job=run_memory_job,
    )
    await harness.record_and_trigger(manager.load(ref.session_id))
    await harness.wait_idle()  # Background failure must not fail the foreground turn.

    import sqlite3

    database = Path(ref.session_dir) / "memory" / "memory.sqlite3"
    with sqlite3.connect(database) as connection:
        pending = connection.execute(
            "SELECT content, build_state FROM uts WHERE id='ut-canary'"
        ).fetchone()
    assert pending == ("The service must use canary releases.", "pending")
    state = json.loads(
        (Path(ref.session_dir) / "state" / "eternal-harness.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["memory_jobs"]["builder"]["status"] == "failed"
    assert state["requested_cursor"] == 2
    assert load_projection(ref.session_dir)["covered_through"] == 2

    # A later foreground trigger restarts only the recoverable memory job. The
    # already-persisted foreground history and the Pending UT remain unchanged.
    await harness.record_and_trigger(manager.load(ref.session_id))
    await harness.wait_idle()

    with sqlite3.connect(database) as connection:
        built = connection.execute(
            "SELECT content, build_state FROM uts WHERE id='ut-canary'"
        ).fetchone()
    assert built == ("The service must use canary releases.", "built")
    state = json.loads(
        (Path(ref.session_dir) / "state" / "eternal-harness.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["memory_jobs"]["builder"]["status"] == "idle"
    assert manager.load(ref.session_id).history == session.history


def test_memory_job_limits_match_the_validated_background_budget() -> None:
    from lara.runtime.agent.common import DEFAULT_REACT_MAX_STEPS
    from lara.runtime.service import MEMORY_JOB_LIMITS

    assert MEMORY_JOB_LIMITS.max_steps == DEFAULT_REACT_MAX_STEPS
    assert MEMORY_JOB_LIMITS.max_model_calls == DEFAULT_REACT_MAX_STEPS
    assert MEMORY_JOB_LIMITS.max_tool_calls == DEFAULT_REACT_MAX_STEPS


@pytest.mark.asyncio
async def test_close_cancels_stuck_job_quickly_and_next_turn_reruns(
    tmp_path: Path,
) -> None:
    config = RunConfig(workspace_root=str(tmp_path), lara_root=str(tmp_path / ".lara"))
    manager = SessionManager(config)
    ref = manager.create("chat", mode="chat")
    session = manager.load(ref.session_id)
    session.history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "one"},
    ]
    manager.save(session)

    gate = asyncio.Event()

    async def stuck_memory_job(request: MemoryJobRequest) -> str:
        await gate.wait()  # stuck until the test releases it
        return "done"

    harness = EternalConversationHarness(
        EternalConversationConfig(enabled=True, dynamic_memory_cli_path=str(SCRIPT)),
        run_memory_job=stuck_memory_job,
    )
    await harness.record_and_trigger(manager.load(ref.session_id))
    await asyncio.sleep(0.05)

    started = asyncio.get_running_loop().time()
    await harness.close(timeout=0.2)
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 5  # bounded shutdown; not blocked by the stuck job
    assert harness._closed is True
    # nothing was published: the coverage cursor stays where it was
    assert load_projection(ref.session_dir).get("covered_through", 0) == 0

    # a later foreground turn re-triggers extraction from the same cursor and
    # a healthy job completes it — no resume machinery needed
    gate.set()
    session = manager.load(ref.session_id)
    session.history.extend(
        [{"role": "user", "content": "second"}, {"role": "assistant", "content": "two"}]
    )
    manager.save(session)
    async def resumed_memory_job(request: MemoryJobRequest) -> str:
        await _publish_via_cli(
            request,
            snapshot={
                "resident_memory": [],
                "recent_context": [],
                "current_state": [],
                "completed": [],
                "next_actions": [],
                "constraints": [],
            },
            changed_uts=[],
            semantic_statement="The uncovered range is represented.",
        )
        return "done"

    resumed = EternalConversationHarness(
        EternalConversationConfig(enabled=True, dynamic_memory_cli_path=str(SCRIPT)),
        run_memory_job=resumed_memory_job,
    )
    await resumed.record_and_trigger(manager.load(ref.session_id))
    await resumed.wait_idle()

    projection = load_projection(ref.session_dir)
    assert projection["covered_through"] == 4
