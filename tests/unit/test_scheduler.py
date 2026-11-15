"""`lara scheduler` and `/scheduler`: lara's face over the SDK scheduler."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pygent.agent.pygent_v1.scheduler.daemon import TaskResult
from pygent.agent.pygent_v1.scheduler.store import SchedulerStore, scheduler_db_path

from lara.cli import build_parser
from lara.cli.scheduler import build_daemon, lara_task_runner, submit_user, task_payload
from lara.schema import RunConfig
from lara_api.container import ApiContext
from lara_api.dependencies import get_api_context
from lara_api.routers import scheduler as scheduler_router


def _config(tmp_path: Path) -> RunConfig:
    return RunConfig(
        workspace_root=str(tmp_path / "workspace"),
        pygent_root=str(tmp_path / "home" / "projects" / "p"),
        user_pygent_root=str(tmp_path / "home"),
    )


class _Runner:
    def __init__(self) -> None:
        self.tasks = []

    def run(self, task) -> TaskResult:
        self.tasks.append(task)
        return TaskResult(exit_code=0, output="ran")


def test_scheduler_cli_registers_the_sdk_task_surface() -> None:
    parser = build_parser()
    add = parser.parse_args(
        ["scheduler", "add", "--name", "n", "--schedule", "every 1h", "--prompt", "p"]
    )
    assert add.scheduler_command == "add"
    assert add.priority == "normal"
    control = parser.parse_args(["scheduler", "pause", "task-1"])
    assert control.action == "pause" and control.task_id == "task-1"
    run = parser.parse_args(["scheduler", "run", "--once"])
    assert run.once is True
    assert parser.parse_args(["scheduler", "emit", "deploy-done"]).event == "deploy-done"
    with pytest.raises(SystemExit):
        parser.parse_args(["automation", "list"])
    with pytest.raises(SystemExit):
        parser.parse_args(["session", "start", "-m", "x"])


def test_user_submission_is_promoted_by_the_daemon_and_runs_through_lara(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    runner = _Runner()
    daemon = build_daemon(config, runner=runner)  # also creates the signing keys

    submission = submit_user(
        config,
        kind="task",
        payload=task_payload(
            name="nightly",
            schedule="at 2000-01-01T00:00:00+00:00",
            prompt="summarize",
            session=None,
            default_session=config.scheduler.session,
        ),
    )
    assert submission["status"] == "unverified"

    counts = daemon.run_once()
    assert counts == {"submissions": 1, "tasks": 1}
    store = SchedulerStore(scheduler_db_path(config.pygent_root))
    [task] = store.list_tasks()
    # User submissions are approved without review and run in the configured
    # scheduler session.
    assert task.status == "approved"
    assert task.session == config.scheduler.session
    assert [item.name for item in runner.tasks] == ["nightly"]
    [run] = store.list_runs(task_id=task.id)
    assert run.status == "success"

    # The real runner spawns lara against the daemon's workspace.
    command = lara_task_runner(config).command
    assert command[:3] == (sys.executable, "-m", "lara")
    assert command[command.index("--workspace-root") + 1] == config.workspace_root


def test_control_submission_pauses_the_task(tmp_path: Path) -> None:
    config = _config(tmp_path)
    daemon = build_daemon(config, runner=_Runner())
    submit_user(
        config,
        kind="task",
        payload=task_payload(
            name="hourly", schedule="every 1h", prompt="check", session=None,
            default_session="scheduler",
        ),
    )
    daemon.run_once()
    store = SchedulerStore(scheduler_db_path(config.pygent_root))
    [task] = store.list_tasks()

    submit_user(config, kind="control", payload={"action": "pause", "task_id": task.id})
    daemon.run_once()

    assert store.task(task.id).status == "paused"


def test_task_payload_validates_the_schedule_before_signing() -> None:
    with pytest.raises(ValueError):
        task_payload(
            name="x", schedule="whenever", prompt="p", session=None, default_session="s"
        )
    with pytest.raises(ValueError):
        task_payload(
            name=" ", schedule="every 1h", prompt="p", session=None, default_session="s"
        )


def test_scheduler_routes_submit_and_read_through_the_sdk_store(tmp_path: Path) -> None:
    config = _config(tmp_path)
    daemon = build_daemon(config, runner=_Runner())
    context = ApiContext(workspace_root=config.workspace_root)
    context._config = config
    app = FastAPI()
    app.include_router(scheduler_router.router)
    app.dependency_overrides[get_api_context] = lambda: context
    client = TestClient(app)

    created = client.post(
        "/scheduler/tasks",
        json={"name": "daily", "schedule": "daily 09:00", "prompt": "report"},
    )
    assert created.status_code == 202
    assert created.json()["kind"] == "task"
    daemon.run_once()

    listed = client.get("/scheduler/tasks").json()["tasks"]
    assert [task["name"] for task in listed] == ["daily"]
    task_id = listed[0]["id"]
    shown = client.get(f"/scheduler/tasks/{task_id}").json()
    assert shown["task"]["schedule"] == "daily 09:00"
    assert shown["runs"] == []

    assert client.post(f"/scheduler/tasks/{task_id}/pause").status_code == 202
    daemon.run_once()
    assert client.get(f"/scheduler/tasks/{task_id}").json()["task"]["status"] == "paused"

    assert client.post(f"/scheduler/tasks/missing/pause").status_code == 404
    bad = client.post(
        "/scheduler/tasks", json={"name": "x", "schedule": "sometime", "prompt": "p"}
    )
    assert bad.status_code == 422
    assert client.post("/scheduler/events", json={"event": "ping"}).status_code == 202
