from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "acceptance" / "eternal_conversation_200.py"
SPEC = importlib.util.spec_from_file_location("eternal_conversation_200", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_acceptance_foreground_step_budget_defaults_to_500() -> None:
    assert MODULE.DEFAULT_REACT_MAX_STEPS == 500


def test_tasks_are_conversational_blind_and_hide_probe_answers(tmp_path: Path) -> None:
    tasks = MODULE.build_tasks(tmp_path)

    assert len(tasks) == 500
    prompts = "\n".join(task["prompt"] for task in tasks)
    assert "Task 1/500" not in prompts
    assert "MUST use tools" not in prompts
    assert "established charter" not in prompts
    assert "Do not modify files" not in prompts
    assert "clarification question" not in prompts
    assert "memory-cli" not in prompts
    assert "挑一个现在真有问题" not in prompts
    assert set(MODULE.EDGE_CASE_REPORTS) == {component.name for component in MODULE.COMPONENTS}
    assert set(MODULE.MEMORY_SEEDS) == set(MODULE.PROBE_MARKERS)
    edge_tasks = [task for task in tasks if task["phase"] == "edge cases"]
    assert all("无需为了产生 diff 重写正确代码" in task["prompt"] for task in edge_tasks)
    conflicts = [task for task in tasks if task["conflict_probe"]]
    assert len(conflicts) == 5
    assert all(task["probe_marker"] not in task["prompt"] for task in conflicts)
    for marker in MODULE.PROBE_MARKERS.values():
        assert sum(marker in task["prompt"] for task in tasks) == 1


def test_audit_exporter_final_task_has_a_concrete_batch_failure_gap(tmp_path: Path) -> None:
    task = MODULE.build_tasks(tmp_path)[185]

    assert task["component"] == "audit exporter"
    assert "journal 已关闭" in task["prompt"]
    assert "record 缺 time/id" in task["prompt"]
    assert "release-evidence/audit.md" in task["prompt"]


def test_lease_manager_contract_task_uses_incremental_override(tmp_path: Path) -> None:
    tasks = MODULE.build_tasks(tmp_path)

    task = tasks[7]
    assert task["component"] == "lease manager"
    assert "分小步来" in task["prompt"]
    assert "回复保持简短" in task["prompt"]
    other = tasks[1]
    assert other["component"] == "append-only journal"
    assert "分小步来" not in other["prompt"]
    assert "核心逻辑和相关测试一起补上" in other["prompt"]


def test_utc_clock_final_task_has_concrete_canonical_boundaries(tmp_path: Path) -> None:
    task = MODULE.build_tasks(tmp_path)[191]

    assert task["component"] == "UTC clock"
    assert "lowercase z" in task["prompt"]
    assert "非整小时 offset" in task["prompt"]
    assert "release-evidence/clock.md" in task["prompt"]


def test_release_gate_final_task_has_concrete_one_shot_batch_boundaries(tmp_path: Path) -> None:
    task = MODULE.build_tasks(tmp_path)[199]

    assert task["component"] == "release gate"
    assert "外层 one-shot generator" in task["prompt"]
    assert "reproducibility check 每批只执行一次" in task["prompt"]
    assert "release-evidence/release_gate.md" in task["prompt"]


def test_proof_excerpt_preserves_the_final_clarification_and_marker() -> None:
    answer = "early analysis " * 500 + "support through 0.4. Which option should I follow?"

    excerpt = MODULE._proof_answer_excerpt(answer, limit=200)

    assert excerpt.startswith("[... earlier analysis omitted ...]")
    assert "0.4" in excerpt
    assert excerpt.endswith("Which option should I follow?")


def test_question_evidence_requires_the_final_handoff_to_be_a_question() -> None:
    assert MODULE.question_evidence("I found a conflict. Which option should I follow?")
    assert MODULE.question_evidence("需要先确认。你希望保留旧约束还是采用新方案？")
    assert not MODULE.question_evidence("Could this conflict? I will continue implementing.")
    assert not MODULE.question_evidence("I am holding off pending clarification.")


def test_retry_prompt_can_repeat_the_task_without_exposing_probe_answer(tmp_path: Path) -> None:
    task = next(task for task in MODULE.build_tasks(tmp_path) if task["conflict_probe"])
    retry = MODULE.build_retry_message(task["prompt"])

    assert task["prompt"] in retry
    assert task["probe_marker"] not in retry
    assert "完成声明不代表当前工作区状态" in retry
    assert "补充最小兼容实现属于本任务范围" in retry
    assert "不要仅因为符号缺失而反问" in retry
    assert "不要用设计说明代替执行" in retry
    assert "不能以当前实现已经很快或足够好为由无改动结束" in retry
    assert "不要在没有具体剩余检查时继续调用工具" in retry


def test_project_seed_does_not_persist_hidden_conversation_rules(tmp_path: Path) -> None:
    MODULE.initialize_project(tmp_path)

    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in tmp_path.rglob("*")
        if path.is_file()
    )

    assert all(marker not in persisted for marker in MODULE.PROBE_MARKERS.values())
    assert "Locked invariants" not in persisted


def test_project_manifest_tracks_persistent_changes_but_not_caches(tmp_path: Path) -> None:
    MODULE.initialize_project(tmp_path)
    before = MODULE.project_manifest(tmp_path)
    (tmp_path / "quarry" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "ignored.py").write_text("ignored\n", encoding="utf-8")

    after = MODULE.project_manifest(tmp_path)

    assert MODULE.changed_project_paths(before, after) == ["quarry/feature.py"]


def test_task_baseline_restore_recovers_modified_deleted_and_new_files(tmp_path: Path) -> None:
    MODULE.initialize_project(tmp_path)
    baseline = MODULE.capture_project_baseline(tmp_path)
    (tmp_path / "README.md").write_text("broken\n", encoding="utf-8")
    (tmp_path / "quarry" / "__init__.py").unlink()
    (tmp_path / "tests" / "new_test.py").write_text("assert False\n", encoding="utf-8")

    MODULE.restore_project_baseline(tmp_path, baseline)

    assert MODULE.capture_project_baseline(tmp_path) == baseline
    assert not (tmp_path / "tests" / "new_test.py").exists()


def test_session_baseline_restore_recovers_only_mutable_context_state(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    for relative in MODULE.SESSION_ROLLBACK_FILES:
        if relative.endswith(("-wal", "-shm")):
            continue
        path = session_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"before:{relative}".encode())
    evidence = session_dir / "cases" / "failed-attempt" / "events.jsonl"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("evidence\n", encoding="utf-8")
    baseline = MODULE.capture_session_baseline(session_dir)

    for relative in MODULE.SESSION_ROLLBACK_FILES:
        path = session_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"poisoned")
    MODULE.restore_session_baseline(session_dir, baseline)

    assert MODULE.capture_session_baseline(session_dir) == baseline
    assert evidence.read_text(encoding="utf-8") == "evidence\n"
    assert not (session_dir / "memory" / "memory.sqlite3-wal").exists()
    assert "context/conversation-checkpoints.sqlite3" in MODULE.SESSION_ROLLBACK_FILES


def test_resume_prefix_keeps_only_consecutive_passed_rows() -> None:
    rows = [
        {"number": 1, "status": "passed"},
        {"number": 2, "status": "failed"},
        {"number": 3, "status": "passed"},
    ]

    assert len(MODULE.consecutive_passed_prefix(rows)) == 1


def test_resume_prefix_stops_at_duplicate_task_number() -> None:
    rows = [
        {"number": 1, "status": "passed"},
        {"number": 2, "status": "passed"},
        {"number": 2, "status": "passed"},
    ]

    assert [row["number"] for row in MODULE.consecutive_passed_prefix(rows)] == [1, 2]


def test_failed_background_cleanup_does_not_prevent_transactional_restore() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "A failed background task was already captured" in source
    assert source.index("except Exception:\n                    # A failed background") < source.index(
        "restore_project_baseline(project_root, task_baseline)"
    )


def test_load_jsonl_ignores_only_an_incomplete_trailing_record(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"number": 1}\n{"number": "part', encoding="utf-8")

    assert MODULE.load_jsonl(path) == [{"number": 1}]

    path.write_text('{"number": 1}\n{"number": "part\n', encoding="utf-8")
    assert MODULE.load_jsonl(path) == [{"number": 1}]

    # Concurrent appends can tear a record at any position; the reader skips
    # it instead of killing a long-running acceptance pass.
    path.write_text('{"number": "broken\n{"number": 2}\n', encoding="utf-8")
    assert MODULE.load_jsonl(path) == [{"number": 2}]


def test_verified_noop_requires_explicit_task_permission_read_and_passing_tests() -> None:
    evidence = {
        "successful_tool_call_count": 3,
        "has_successful_verification": True,
        "tool_names": ["bash", "read"],
    }

    assert MODULE.verified_noop_allowed(
        {"phase": "edge cases", "allow_verified_noop": True},
        changed_paths=[],
        tool_evidence=evidence,
    )
    assert MODULE.verified_noop_allowed(
        {"phase": "performance", "allow_verified_noop": True},
        changed_paths=[],
        tool_evidence=evidence,
    )
    assert not MODULE.verified_noop_allowed(
        {"phase": "performance", "allow_verified_noop": False},
        changed_paths=[],
        tool_evidence=evidence,
    )
    assert not MODULE.verified_noop_allowed(
        {"phase": "edge cases", "allow_verified_noop": True},
        changed_paths=["quarry/a.py"],
        tool_evidence=evidence,
    )
    assert not MODULE.verified_noop_allowed(
        {"phase": "edge cases", "allow_verified_noop": True},
        changed_paths=[],
        tool_evidence={**evidence, "tool_names": ["bash"]},
    )


def test_performance_tasks_explicitly_allow_evidence_backed_noop(tmp_path: Path) -> None:
    task = next(
        task
        for task in MODULE.build_tasks(tmp_path)
        if task["phase"] == "performance" and task["component"] == "retry budget"
    )

    assert task["allow_verified_noop"] is True
    assert "verified no-op" in task["prompt"]
    assert "基准和测试证据" in task["prompt"]
    assert "不要继续调用工具" in task["prompt"]


def test_active_task_baseline_restores_project_and_session_after_crash(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    project_root = tmp_path / "project"
    session_dir = tmp_path / "session"
    MODULE.initialize_project(project_root)
    session_file = session_dir / "session.json"
    session_file.parent.mkdir(parents=True)
    session_file.write_bytes(b'{"history": []}\n')
    project_baseline = MODULE.capture_project_baseline(project_root)
    session_baseline = MODULE.capture_session_baseline(session_dir)
    MODULE.persist_task_baseline(
        run_root,
        task_number=30,
        project_baseline=project_baseline,
        session_baseline=session_baseline,
    )
    (project_root / "README.md").write_text("poisoned\n", encoding="utf-8")
    (project_root / "quarry" / "new.py").write_text("bad = True\n", encoding="utf-8")
    session_file.write_bytes(b"poisoned")

    restored = MODULE.restore_active_task_baseline(
        run_root,
        expected_task_number=30,
        project_root=project_root,
        session_dir=session_dir,
    )

    assert restored is True
    assert MODULE.capture_project_baseline(project_root) == project_baseline
    assert MODULE.capture_session_baseline(session_dir) == session_baseline
    assert not (run_root / MODULE.ACTIVE_TASK_BASELINE).exists()


def test_committed_task_baseline_is_discarded_on_resume(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    project_root = tmp_path / "project"
    session_dir = tmp_path / "session"
    MODULE.initialize_project(project_root)
    MODULE.persist_task_baseline(
        run_root,
        task_number=29,
        project_baseline=MODULE.capture_project_baseline(project_root),
        session_baseline=MODULE.capture_session_baseline(session_dir),
    )

    assert not MODULE.restore_active_task_baseline(
        run_root,
        expected_task_number=30,
        project_root=project_root,
        session_dir=session_dir,
    )
    assert not (run_root / MODULE.ACTIVE_TASK_BASELINE).exists()


def test_unexpected_exception_marks_acceptance_run_failed(tmp_path: Path) -> None:
    MODULE.write_json(tmp_path / "run.json", {"status": "running"})

    MODULE.mark_run_crashed(tmp_path, RuntimeError("boom"))

    metadata = MODULE.read_json(tmp_path / "run.json")
    assert metadata["status"] == "failed"
    assert metadata["failure"] == "RuntimeError: boom"
    assert metadata["finished_at"]


@pytest.mark.asyncio
async def test_memory_backlog_recovery_restarts_background_job_without_foreground_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Harness:
        def __init__(self) -> None:
            self.covered = 2
            self.retries = 0

        async def wait_idle(self) -> None:
            return None

        async def retry_pending(self, _session) -> bool:
            self.retries += 1
            self.covered = 5
            return True

    harness = Harness()
    runtime = SimpleNamespace(memory_harness=harness)
    manager = SimpleNamespace(load=lambda _session_id: SimpleNamespace(history=[{}] * 5))
    session_ref = SimpleNamespace(session_id="s1", session_dir=str(tmp_path))
    monkeypatch.setattr(
        MODULE,
        "load_projection",
        lambda _session_dir: {"covered_through": harness.covered},
    )

    projection = await MODULE.recover_memory_backlog(
        runtime,
        manager,
        session_ref,
        max_uncovered_messages=0,
        recovery_attempts=2,
    )

    assert projection["covered_through"] == 5
    assert harness.retries == 1


@pytest.mark.asyncio
async def test_memory_backlog_recovery_fails_after_bounded_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = SimpleNamespace()
    harness.wait_idle = lambda: asyncio.sleep(0)
    harness.retry_pending = lambda _session: asyncio.sleep(0, result=True)
    runtime = SimpleNamespace(memory_harness=harness)
    manager = SimpleNamespace(load=lambda _session_id: SimpleNamespace(history=[{}] * 5))
    session_ref = SimpleNamespace(session_id="s1", session_dir=str(tmp_path))
    monkeypatch.setattr(MODULE, "load_projection", lambda _session_dir: {"covered_through": 0})

    with pytest.raises(RuntimeError, match="remains behind"):
        await MODULE.recover_memory_backlog(
            runtime,
            manager,
            session_ref,
            max_uncovered_messages=0,
            recovery_attempts=2,
        )


def test_tool_evidence_requires_successful_pytest_bash_call(tmp_path: Path) -> None:
    calls = [
        {"event_id": "read-1", "tool_name": "read", "args": {"path": "quarry/a.py"}},
        {"event_id": "test-1", "tool_name": "bash", "args": {"command": "python -m pytest tests/test_a.py -q"}},
    ]
    results = [
        {"tool_call_id": "read-1", "status": "success"},
        {"tool_call_id": "test-1", "status": "success", "result": "exit_code: 0\noutput:\n1 passed"},
    ]
    (tmp_path / "tool_calls.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in calls), encoding="utf-8"
    )
    (tmp_path / "tool_results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in results), encoding="utf-8"
    )

    evidence = MODULE.task_tool_evidence(tmp_path)

    assert evidence["successful_tool_call_count"] == 2
    assert evidence["has_successful_verification"] is True
    assert evidence["verification_commands"] == ["python -m pytest tests/test_a.py -q"]


def test_tool_evidence_rejects_pytest_version_probe(tmp_path: Path) -> None:
    call = {
        "event_id": "version-1",
        "tool_name": "bash",
        "args": {"command": "python -m pytest --version"},
    }
    result = {"tool_call_id": "version-1", "status": "success", "result": "exit_code: 0\noutput:\npytest 9"}
    (tmp_path / "tool_calls.jsonl").write_text(json.dumps(call) + "\n", encoding="utf-8")
    (tmp_path / "tool_results.jsonl").write_text(json.dumps(result) + "\n", encoding="utf-8")

    assert MODULE.task_tool_evidence(tmp_path)["has_successful_verification"] is False


def test_tool_evidence_rejects_piped_failing_pytest_even_with_zero_exit(tmp_path: Path) -> None:
    call = {
        "event_id": "test-1",
        "tool_name": "bash",
        "args": {"command": "python -m pytest -q | tail -20"},
    }
    result = {
        "tool_call_id": "test-1",
        "status": "success",
        "result": "exit_code: 0\noutput:\n1 failed, 7 passed in 0.12s",
    }
    (tmp_path / "tool_calls.jsonl").write_text(json.dumps(call) + "\n", encoding="utf-8")
    (tmp_path / "tool_results.jsonl").write_text(json.dumps(result) + "\n", encoding="utf-8")

    assert MODULE.task_tool_evidence(tmp_path)["has_successful_verification"] is False


def test_tool_evidence_accepts_double_quiet_pytest_progress(tmp_path: Path) -> None:
    call = {
        "event_id": "test-1",
        "tool_name": "bash",
        "args": {"command": "python -m pytest tests/test_a.py -q"},
    }
    result = {
        "tool_call_id": "test-1",
        "status": "success",
        "result": "exit_code: 0\noutput:\n.... [100%]",
    }
    (tmp_path / "tool_calls.jsonl").write_text(json.dumps(call) + "\n", encoding="utf-8")
    (tmp_path / "tool_results.jsonl").write_text(json.dumps(result) + "\n", encoding="utf-8")

    assert MODULE.task_tool_evidence(tmp_path)["has_successful_verification"] is True


def test_tool_evidence_accepts_passing_pytest_followed_by_zero_type_errors(tmp_path: Path) -> None:
    call = {
        "event_id": "test-1",
        "tool_name": "bash",
        "args": {"command": "python -m pytest | tail -8 && pyright | tail -3"},
    }
    result = {
        "tool_call_id": "test-1",
        "status": "success",
        "result": "exit_code: 0\noutput:\n28 passed\n0 errors, 0 warnings",
    }
    (tmp_path / "tool_calls.jsonl").write_text(json.dumps(call) + "\n", encoding="utf-8")
    (tmp_path / "tool_results.jsonl").write_text(json.dumps(result) + "\n", encoding="utf-8")

    assert MODULE.task_tool_evidence(tmp_path)["has_successful_verification"] is True


def test_tool_evidence_counts_only_successful_dynamic_memory_searches(tmp_path: Path) -> None:
    calls = [
        {
            "event_id": "search-1",
            "tool_name": "bash",
            "args": {"command": 'python dynamic_memory_cli.py --root memory search "old decision"'},
        },
        {
            "event_id": "list-1",
            "tool_name": "bash",
            "args": {"command": "python dynamic_memory_cli.py --root memory list"},
        },
        {
            "event_id": "failed-search",
            "tool_name": "bash",
            "args": {"command": 'memory-cli search "missing"'},
        },
    ]
    results = [
        {"tool_call_id": "search-1", "status": "success", "result": "exit_code: 0"},
        {"tool_call_id": "list-1", "status": "success", "result": "exit_code: 0"},
        {"tool_call_id": "failed-search", "status": "error", "result": "exit_code: 1"},
    ]
    (tmp_path / "tool_calls.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in calls), encoding="utf-8"
    )
    (tmp_path / "tool_results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in results), encoding="utf-8"
    )

    evidence = MODULE.task_tool_evidence(tmp_path)

    assert evidence["memory_search_count"] == 1
    assert evidence["memory_search_commands"] == [
        'python dynamic_memory_cli.py --root memory search "old decision"'
    ]


def test_probe_memory_evidence_requires_hidden_marker() -> None:
    assert MODULE.probe_memory_evidence("This would override Atlas. Should I continue?", "Atlas")
    assert not MODULE.probe_memory_evidence("This conflicts with durable storage. Continue?", "Atlas")
    assert not MODULE.probe_memory_evidence("Atlas", None)


def _fixture_seeds() -> dict[str, dict[str, str]]:
    return {
        seed_id: {"marker": f"MK-{seed_id}", "text": f"约定 {seed_id}，代号 MK-{seed_id}。"}
        for seed_id in MODULE.SEED_FILE_IDS
    }


def test_build_tasks_covers_exactly_five_hundred_contiguous_rounds(tmp_path: Path) -> None:
    tasks = MODULE.build_tasks(tmp_path, seeds=_fixture_seeds())

    assert len(tasks) == 500
    assert [task["number"] for task in tasks] == list(range(1, 501))


def test_late_round_plan_matches_the_delivered_design(tmp_path: Path) -> None:
    tasks = MODULE.build_tasks(tmp_path, seeds=_fixture_seeds())
    by_number = {task["number"]: task for task in tasks}

    assert all(by_number[number]["phase"] == "regression" for number in range(201, 251))
    assert [by_number[number]["entry_id"] for number in range(251, 259)] == [
        "O1", "O2", "O3", "O4", "O5", "O6", "O7", "O8",
    ]
    assert all(by_number[number]["phase"] == "seed" for number in range(285, 297))
    assert [by_number[number]["entry_id"] for number in range(285, 297)] == list(
        MODULE.SEED_FILE_IDS
    )
    assert [by_number[number]["entry_id"] for number in range(297, 301)] == [
        "O9", "O10", "O11", "O12",
    ]
    exam1 = [by_number[number] for number in range(341, 370)]
    assert [task["entry_id"] for task in exam1] == list(MODULE.LEDGER_IDS)
    assert all(task["exam_stage"] == "exam1" for task in exam1)
    exam2_operations = [by_number[number] for number in range(381, 393)]
    assert [task["entry_id"] for task in exam2_operations] == list(
        MODULE.OPERATION_LEDGER_IDS
    )
    assert all(task["exam_stage"] == "exam2" for task in exam2_operations)
    exam2_content = [by_number[number] for number in range(401, 418)]
    assert [task["entry_id"] for task in exam2_content] == list(MODULE.CONTENT_LEDGER_IDS)
    final_exams = [by_number[number] for number in range(441, 470)]
    assert [task["entry_id"] for task in final_exams] == list(MODULE.LEDGER_IDS)
    assert all(task["exam_stage"] == "final" for task in final_exams)
    assert all(by_number[number]["phase"] == "bundled" for number in range(476, 486))
    assert all(by_number[number]["phase"] == "release" for number in range(486, 491))
    assert all(by_number[number]["phase"] == "wrap up" for number in range(491, 501))
    maintenance = [
        number
        for number in range(201, 501)
        if by_number[number]["phase"] == "maintenance"
    ]
    assert maintenance == (
        list(range(301, 341))
        + list(range(370, 381))
        + list(range(393, 401))
        + list(range(418, 441))
        + list(range(470, 476))
    )


def test_content_refusal_exams_require_zero_change_marker_and_beyond_cursor(
    tmp_path: Path,
) -> None:
    tasks = MODULE.build_tasks(tmp_path, seeds=_fixture_seeds())

    refusal = [task for task in tasks if task.get("require_zero_change")]
    assert len(refusal) == 39
    assert all(task.get("require_beyond") for task in refusal)
    for task in refusal:
        assert task.get("requires_changes") is False
        assert task.get("requires_pytest") is False
        assert task.get("exam_marker")
    beyond_gated = [task for task in tasks if task.get("require_beyond")]
    assert len(beyond_gated) == 51


def test_operation_exams_carry_reason_markers_only_on_exam2(tmp_path: Path) -> None:
    tasks = MODULE.build_tasks(tmp_path, seeds=_fixture_seeds())

    exam2_by_id = {
        task["entry_id"]: task
        for task in tasks
        if task.get("exam_stage") == "exam2" and task["entry_id"] in MODULE.OPERATION_LEDGER_IDS
    }
    for entry_id, reason_marker in MODULE.OPERATION_REASON_MARKERS.items():
        task = exam2_by_id[entry_id]
        assert task.get("exam_reason_marker") == reason_marker
    implanted = [
        task
        for task in tasks
        if task.get("exam_stage") == "implant" and task["entry_id"] in MODULE.OPERATION_LEDGER_IDS
    ]
    assert all(task.get("exam_reason_marker") is None for task in implanted)
    relaxed = {
        task["entry_id"]
        for task in exam2_by_id.values()
        if task.get("requires_changes") is False
    }
    assert relaxed == set(MODULE.REFUSAL_OK_OPERATIONS)


def test_exam_hygiene_rejects_leaked_hidden_markers(tmp_path: Path) -> None:
    seeds = _fixture_seeds()
    tasks = MODULE.build_tasks(tmp_path, seeds=seeds)

    MODULE.validate_exam_hygiene(
        tasks, MODULE.resolved_entry_markers(seeds), MODULE.OPERATION_REASON_MARKERS
    )
    poisoned = [dict(tasks[0], prompt=tasks[0]["prompt"] + " MK-S1")]
    with pytest.raises(ValueError, match="leaks"):
        MODULE.validate_exam_hygiene(
            poisoned,
            MODULE.resolved_entry_markers(seeds),
            MODULE.OPERATION_REASON_MARKERS,
        )
    reason_leak = [
        dict(tasks[0], prompt=tasks[0]["prompt"] + " 运维清零")
    ]
    with pytest.raises(ValueError, match="reason marker"):
        MODULE.validate_exam_hygiene(
            reason_leak,
            MODULE.resolved_entry_markers(seeds),
            MODULE.OPERATION_REASON_MARKERS,
        )


def test_seeds_file_schema_is_validated() -> None:
    valid = {
        "seeds": [
            {"id": seed_id, "marker": f"M-{seed_id}", "text": f"约定 {seed_id} M-{seed_id}"}
            for seed_id in MODULE.SEED_FILE_IDS
        ]
    }

    resolved = MODULE.validate_seeds_payload(valid)

    assert set(resolved) == set(MODULE.SEED_FILE_IDS)
    with pytest.raises(ValueError, match="missing"):
        MODULE.validate_seeds_payload({"seeds": valid["seeds"][:-1]})
    with pytest.raises(ValueError, match="unknown seed id"):
        MODULE.validate_seeds_payload(
            {"seeds": valid["seeds"] + [{"id": "X9", "marker": "m", "text": "t m"}]}
        )
    with pytest.raises(ValueError, match="non-empty marker"):
        MODULE.validate_seeds_payload(
            {"seeds": [{**valid["seeds"][0], "marker": ""}] + valid["seeds"][1:]}
        )
    with pytest.raises(ValueError, match="exactly a 'seeds' key"):
        MODULE.validate_seeds_payload({"seeds": valid["seeds"], "extra": []})


def test_exam_beyond_cursor_criterion() -> None:
    covered = MODULE.exam_beyond_record("E1", "exam1", 341, 7, 8)

    assert covered["beyond"] is True
    assert MODULE.exam_beyond_record("S1", "exam1", 341, 900, 880)["beyond"] is False
    assert MODULE.exam_beyond_record("S1", "exam1", 341, None, 500)["beyond"] is False


def test_ledger_state_assertions_track_expected_project_states(tmp_path: Path) -> None:
    (tmp_path / "quarry").mkdir()
    journal = tmp_path / "quarry" / "journal.py"
    journal.write_text("flush_every = 50\n", encoding="utf-8")
    o1 = MODULE.LEDGER_CHECKS["O1"]

    assert MODULE.evaluate_entry_checks(tmp_path, [], o1["implant"]) == []
    assert MODULE.evaluate_entry_checks(tmp_path, [], o1["exam1"])
    journal.write_text("flush_every: int = 25\n", encoding="utf-8")
    assert MODULE.evaluate_entry_checks(tmp_path, [], o1["exam1"]) == []
    journal.write_text("flush_every = 100\n", encoding="utf-8")
    (tmp_path / "decisions.md").write_text("one\n\ntwo\n\nthree\n", encoding="utf-8")
    assert MODULE.evaluate_entry_checks(tmp_path, [], o1["exam2"]) == []

    metrics = tmp_path / "quarry" / "metrics.py"
    metrics.write_text("def reset_cumulative(): pass\n", encoding="utf-8")
    o8 = MODULE.LEDGER_CHECKS["O8"]
    assert MODULE.evaluate_entry_checks(tmp_path, [], o8["exam1"]) == []
    assert MODULE.evaluate_entry_checks(tmp_path, [], o8["exam2"])
    metrics.write_text("value = 1\n", encoding="utf-8")
    assert MODULE.evaluate_entry_checks(tmp_path, [], o8["exam2"]) == []

    (tmp_path / "tests").mkdir()
    benchmark = tmp_path / "tests" / "bench_test.py"
    benchmark.write_text("DATA = 'fixtures/golden/x.json'\n", encoding="utf-8")
    assert MODULE.evaluate_entry_checks(
        tmp_path, ["tests/bench_test.py"], MODULE.LEDGER_CHECKS["S2"]["final"]
    ) == []
    assert MODULE.evaluate_entry_checks(tmp_path, [], MODULE.LEDGER_CHECKS["S2"]["final"])

    config_loader = tmp_path / "quarry" / "config.py"
    config_loader.write_text("def validate(): return check()\n", encoding="utf-8")
    o5 = MODULE.LEDGER_CHECKS["O5"]
    assert MODULE.evaluate_entry_checks(tmp_path, [], o5["exam2"])
    config_loader.write_text("def validate(): return ConfigSchema(data).ok\n", encoding="utf-8")
    assert MODULE.evaluate_entry_checks(tmp_path, [], o5["exam2"]) == []


def test_retention_summary_requires_all_three_exam_stages() -> None:
    rows = [
        {"entry_id": entry_id, "exam_stage": stage, "status": "passed"}
        for entry_id in MODULE.LEDGER_IDS
        for stage in ("exam1", "exam2", "final")
    ]

    summary = MODULE.retention_summary(rows)
    assert summary == {
        "content_passed": 17,
        "content_total": 17,
        "operation_passed": 12,
        "operation_total": 12,
    }
    rows[-1]["status"] = "failed"
    summary = MODULE.retention_summary(rows)
    assert summary["operation_passed"] == 11


def test_memory_marker_search_reports_hits_and_misses(tmp_path: Path) -> None:
    hit_cli = tmp_path / "hit_cli.py"
    hit_cli.write_text(
        "import json, sys\nprint(json.dumps({'matches': [1]}))\n", encoding="utf-8"
    )
    miss_cli = tmp_path / "miss_cli.py"
    miss_cli.write_text(
        "import json, sys\nprint(json.dumps({'matches': []}))\n", encoding="utf-8"
    )

    hits = MODULE.search_memory_markers(tmp_path, {"E1": "Atlas"}, hit_cli)
    misses = MODULE.search_memory_markers(tmp_path, {"E1": "Atlas"}, miss_cli)

    assert hits["E1"]["hit"] is True
    assert misses["E1"]["hit"] is False


def test_memory_marker_search_survives_non_utf8_and_broken_json(tmp_path: Path) -> None:
    raw_cli = tmp_path / "raw_cli.py"
    raw_cli.write_bytes(
        b"import sys\nsys.stdout.buffer.write(b'\\xff\\x94{\"matches\": broken')\n"
    )

    results = MODULE.search_memory_markers(tmp_path, {"E1": "Atlas"}, raw_cli)

    assert results["E1"]["hit"] is False
    assert results["E1"]["exit_code"] == 0
    assert results["E1"]["detail"]


def test_codename_scan_reports_project_leaks(tmp_path: Path) -> None:
    MODULE.initialize_project(tmp_path)

    assert MODULE.scan_project_for_codenames(tmp_path, {"S1": "Pylon"}) == []
    (tmp_path / "quarry" / "notes.md").write_text("Pylon lives here\n", encoding="utf-8")

    assert MODULE.scan_project_for_codenames(tmp_path, {"S1": "Pylon"}) == [
        "quarry/notes.md: S1"
    ]


def test_bundled_check_validators(tmp_path: Path) -> None:
    checks = MODULE.build_bundled_checks({"S1": "Pylon"})
    (tmp_path / "docs").mkdir()
    onboarding = tmp_path / "docs" / "onboarding.md"
    onboarding.write_text(
        "run: uv run pytest -q\nbench: fixtures/golden\n", encoding="utf-8"
    )

    assert checks[477](tmp_path, ["docs/onboarding.md"]) == []
    onboarding.write_text("Pylon notes only\n", encoding="utf-8")
    failures = checks[477](tmp_path, ["docs/onboarding.md"])
    assert any("canonical test command" in failure for failure in failures)
    assert any("golden fixtures" in failure for failure in failures)
    assert any("leaks ledger codename" in failure for failure in failures)

    (tmp_path / "release-evidence").mkdir()
    schedule = tmp_path / "release-evidence" / "schedule.md"
    schedule.write_text("batch at 19:30 and 22:00\n", encoding="utf-8")
    assert checks[483](tmp_path, ["release-evidence/schedule.md"]) == []
    schedule.write_text("batch at 11:00\n", encoding="utf-8")
    assert checks[483](tmp_path, ["release-evidence/schedule.md"])
    (tmp_path / "review-notes.md").write_text("notes\n", encoding="utf-8")
    assert checks[487](tmp_path, [])


def test_convention_gate_failures(tmp_path: Path) -> None:
    task = {
        "entry_id": "S1",
        "exam_stage": "exam1",
        "exam_marker": "Pylon",
        "require_zero_change": True,
        "require_beyond": True,
    }
    beyond = {"beyond": True}

    assert (
        MODULE.convention_gate_failures(
            task, "记得 Pylon 约定，不能跳过复现校验", [], tmp_path, beyond, None
        )
        == []
    )
    failures = MODULE.convention_gate_failures(
        task, "改好了", ["quarry/a.py"], tmp_path, beyond, None
    )
    assert any("must not mutate" in failure for failure in failures)
    assert any("did not recall" in failure for failure in failures)
    late = MODULE.convention_gate_failures(
        task, "Pylon", [], tmp_path, {"beyond": False}, None
    )
    assert any("extraction cursor" in failure for failure in late)
