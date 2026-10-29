from __future__ import annotations

import argparse
import asyncio
import io
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest

from lara.cli import build_parser, main
from lara.cli.sessions import (
    _interactive_session_chat,
    _session_fork,
    _session_start,
    _turn_result_payload,
)
from lara.config import load_run_config
from lara.dispatch import DispatchStore
from lara.schema import CaseRunRef, RunConfig, default_cli_bash_presets
from lara.sessions import SessionManager


def test_main_prints_json_without_escaping_non_ascii() -> None:
    parser = argparse.ArgumentParser()
    parser.set_defaults(handler=lambda args: {"final_answer": "开发指南"})
    stdout = io.StringIO()
    with (
        patch("lara.cli.main.build_parser", return_value=parser),
        patch("sys.stdout", stdout),
    ):
        assert main([]) == 0
    assert '"final_answer": "开发指南"' in stdout.getvalue()


def test_session_run_is_the_only_noninteractive_chat_entry() -> None:
    parser = build_parser()
    args = parser.parse_args(["session", "run", "--new", "-m", "hello"])
    assert args.command == "session"
    assert args.session_command == "run"
    assert args.new is True
    assert args.message == "hello"

    with pytest.raises(SystemExit) as error:
        parser.parse_args(["chat", "--new", "-m", "hello"])
    assert error.value.code == 2

    with pytest.raises(SystemExit) as error:
        parser.parse_args(["session", "resume", "session-1"])
    assert error.value.code == 2


def test_default_cli_context_exposes_only_current_session_commands() -> None:
    presets = {preset.name: preset for preset in default_cli_bash_presets()}

    assert presets["lara-session"].command == "uv run lara session --help"
    assert presets["lara-automation"].command == "uv run lara automation --help"
    assert "create|list|show|update|pause|resume|delete|run|runs" in presets[
        "lara-automation"
    ].description
    assert "--session" in presets["lara-automation"].description
    assert "--standalone" in presets["lara-automation"].description
    assert "lara-chat" not in presets
    assert all("lara chat" not in preset.command for preset in presets.values())


def test_session_collaboration_commands_are_registered() -> None:
    parser = build_parser()

    send = parser.parse_args(
        ["session", "send", "target-1", "-m", "hello", "--submission-id", "stable-1"]
    )
    start = parser.parse_args(["session", "start", "-m", "background task"])
    status = parser.parse_args(["session", "status", "op-1"])
    wait = parser.parse_args(["session", "wait", "op-1"])
    listing = parser.parse_args(["session", "list", "--mode", "agent"])

    assert (send.session_id, send.submission_id) == ("target-1", "stable-1")
    assert start.message == "background task"
    assert status.collaboration_id == wait.collaboration_id == "op-1"
    assert listing.mode == "agent"


def test_session_fork_command_accepts_optional_message() -> None:
    parser = build_parser()

    plain = parser.parse_args(["session", "fork", "session-1"])
    started = parser.parse_args(
        ["session", "fork", "session-1", "-m", "branch task", "--submission-id", "stable-1"]
    )

    assert plain.session_id == "session-1"
    assert plain.message is None
    assert (started.session_id, started.message, started.submission_id) == (
        "session-1",
        "branch task",
        "stable-1",
    )


def test_session_start_and_fork_submit_dispatch_items_without_a_worker(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    dispatch_path = tmp_path / ".lara" / "dispatch-v1.sqlite3"

    start_payload = _session_start(
        argparse.Namespace(
            workspace_root=str(workspace),
            agent_alias=None,
            message="background task",
            submission_id="start-call",
            source_session=None,
            model_group=None,
            max_steps=None,
        )
    )

    assert start_payload["operation_id"].startswith("op-")
    assert start_payload["submission_id"] == "start-call"
    assert start_payload["status"] == "queued"
    store = DispatchStore(dispatch_path)
    start_item = store.get_item(start_payload["operation_id"])
    assert start_item.kind == "start"
    assert start_item.message == "background task"
    assert start_item.task_id is None
    assert start_item.state.value == "queued"

    source_session = SessionManager(
        load_run_config(workspace_root=str(workspace))
    ).create("chat", mode="chat").session_id
    fork_payload = _session_fork(
        argparse.Namespace(
            workspace_root=str(workspace),
            agent_alias=None,
            session_id=source_session,
            message="branch task",
            submission_id="fork-call",
            source_session=None,
            model_group=None,
            max_steps=None,
        )
    )

    fork_item = store.get_item(fork_payload["operation_id"])
    assert fork_item.kind == "fork"
    assert fork_item.source_session_id == source_session
    assert fork_item.message == "branch task"


def test_turn_result_payload_is_the_cli_result_contract() -> None:
    run_ref = CaseRunRef(
        session_id="s1",
        case_id="chat",
        case_run_id="r1",
        run_dir="runs/r1",
    )
    turn = SimpleNamespace(
        run_ref=run_ref,
        execution_id="execution-1",
        status="passed",
    )
    output = SimpleNamespace(
        data={
            "result": {
                "status": "passed",
                "final_answer": "agent answer",
                "error": None,
            }
        }
    )

    assert _turn_result_payload(cast(Any, turn), output) == {
        "session_id": "s1",
        "case_run_id": "r1",
        "execution_id": "execution-1",
        "status": "passed",
        "final_answer": "agent answer",
        "run_dir": str(Path("runs/r1").resolve()),
    }


def test_interactive_session_chat_submits_one_managed_turn_per_input(
    tmp_path: Path,
) -> None:
    submitted: list[dict[str, object]] = []
    prewarmed: list[str] = []

    class Turn:
        execution_handle = None

        async def wait_ready(self) -> None:
            return None

        async def result(self):
            return (
                SimpleNamespace(
                    data={
                        "result": {
                            "status": "passed",
                            "final_answer": "ok",
                            "error": None,
                        }
                    }
                ),
                None,
            )

    class Turns:
        async def prewarm_session(self, *, session_id: str, **_kwargs) -> None:
            prewarmed.append(session_id)

        async def submit(self, **kwargs):
            submitted.append(kwargs)
            return Turn()

    class Host:
        turns = Turns()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    config = RunConfig(workspace_root=str(tmp_path), lara_root=str(tmp_path / ".lara"))
    args = argparse.Namespace(
        workspace_root=str(tmp_path),
        session_id=None,
        agent_alias=None,
        max_steps=None,
        new=True,
    )

    with (
        patch("lara.cli.sessions.load_run_config", return_value=config),
        patch("lara.cli.sessions.LocalExecutionHost", return_value=Host()),
        patch("builtins.input", side_effect=["first", "second", "/exit"]),
    ):
        assert asyncio.run(_interactive_session_chat(args)) is None

    assert len(submitted) == 2
    assert submitted[0]["turn_id"] == "turn-0001"
    assert submitted[1]["turn_id"] == "turn-0002"
    assert submitted[0]["session_id"] == submitted[1]["session_id"]
    assert prewarmed == [submitted[0]["session_id"]]
