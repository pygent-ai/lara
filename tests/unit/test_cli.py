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
    _turn_result_payload,
)
from lara.config import load_run_config
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
    assert presets["lara-scheduler"].command == "uv run lara scheduler --help"
    assert "approve|reject|pause|resume|delete <task_id>" in presets[
        "lara-scheduler"
    ].description
    assert "every <n>[smhd], at <ISO>, daily HH:MM, on <event>" in presets[
        "lara-scheduler"
    ].description
    assert "lara-automation" not in presets
    assert "lara-chat" not in presets
    assert all("lara chat" not in preset.command for preset in presets.values())


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

    class Host:
        async def deliver_user_message(self, **kwargs):
            submitted.append(kwargs)
            return SimpleNamespace(mode="turn", turn=Turn(), outcome=None)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    config = RunConfig(workspace_root=str(tmp_path), pygent_root=str(tmp_path / ".lara"))
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

    # Every input goes through the SDK's unified entry on the same session.
    assert [item["message"] for item in submitted] == ["first", "second"]
    assert submitted[0]["session_id"] == submitted[1]["session_id"]
    assert all(item["interactive_approvals"] is True for item in submitted)
