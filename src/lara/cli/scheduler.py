"""`lara scheduler`: the CLI face of the SDK scheduler.

The SDK ships the store, the signed submission protocol and the daemon
(`pygent.agent.pygent_v1.scheduler`) but no command surface. This module
appends signed `user` submissions, reads tasks/runs, and runs the daemon with
`lara session run` as its task runner.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from pygent.agent.pygent_v1.scheduler.daemon import SchedulerDaemon, SubprocessTaskRunner
from pygent.agent.pygent_v1.scheduler.keys import init_keys, load_secret, sign
from pygent.agent.pygent_v1.scheduler.schedule import parse_schedule
from pygent.agent.pygent_v1.scheduler.store import (
    SchedulerStore,
    scheduler_db_path,
    submission_payload,
)

from lara.config import load_run_config
from lara.schema import RunConfig

CONTROL_ACTIONS = ("approve", "reject", "pause", "resume", "delete")


def lara_task_runner(config: RunConfig) -> SubprocessTaskRunner:
    """Task runner that drives one `lara session run` per task.

    The SDK default launches `python -m pygent_v1`; lara owns the session CLI,
    so the daemon must spawn lara against the daemon's workspace.
    """

    return SubprocessTaskRunner(
        command=(
            sys.executable,
            "-m",
            "lara",
            "--workspace-root",
            config.workspace_root,
            "--agent",
            config.agent_alias,
        ),
        user_identity=config.user_identity,
    )


def build_daemon(config: RunConfig, **kwargs: Any) -> SchedulerDaemon:
    init_keys(config.user_pygent_root)
    kwargs.setdefault("runner", lara_task_runner(config))
    return SchedulerDaemon(config, **kwargs)


def submit_user(
    config: RunConfig, *, kind: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Append one signed `user` submission; the daemon promotes it on its next poll."""

    secret = load_secret(config.user_pygent_root, "user")
    store = SchedulerStore(scheduler_db_path(config.pygent_root))
    submission = store.submit(
        kind=kind,
        source="user",
        payload=payload,
        signature=sign(submission_payload(kind, "user", payload), secret),
    )
    return submission.to_dict()


def task_payload(
    *,
    name: str,
    schedule: str,
    prompt: str,
    instructions: str = "",
    session: str | None,
    priority: str = "normal",
    delivery: str | None = None,
    default_session: str,
) -> dict[str, Any]:
    if not name.strip():
        raise ValueError("task name must be non-empty")
    if not prompt.strip():
        raise ValueError("task prompt must be non-empty")
    parse_schedule(schedule)
    payload: dict[str, Any] = {
        "name": name,
        "schedule": schedule,
        "prompt": prompt,
        "instructions": instructions,
        "session": session or default_session,
        "priority": priority,
    }
    if delivery is not None:
        payload["delivery"] = delivery
    return payload


def register_scheduler_parser(subparsers: Any) -> None:
    scheduler = subparsers.add_parser(
        "scheduler", help="Scheduled tasks: the SDK scheduler daemon and its task table"
    )
    commands = scheduler.add_subparsers(dest="scheduler_command", required=True)

    run = commands.add_parser("run", help="Run the scheduler daemon in the foreground")
    run.add_argument(
        "--once", action="store_true", help="Process one poll and exit (for scripts)"
    )
    run.set_defaults(handler=_run)

    init = commands.add_parser("init-keys", help="Create the user/agent signing keys")
    init.add_argument("--force", action="store_true", help="Rotate existing keys")
    init.set_defaults(handler=_init_keys)

    add = commands.add_parser("add", help="Submit a task (every <n>[smhd] | at <ISO> | daily HH:MM | on <event>)")
    add.add_argument("--name", required=True)
    add.add_argument("--schedule", required=True)
    _add_prompt(add)
    add.add_argument("--instructions", default="")
    add.add_argument("--session", default=None, help="Session id to run in; defaults to scheduler.session")
    add.add_argument("--priority", choices=("high", "normal", "low"), default="normal")
    add.add_argument("--delivery", choices=("sync-reply", "speak", "fire-and-forget"), default=None)
    add.set_defaults(handler=_add)

    list_command = commands.add_parser("list", help="List tasks")
    list_command.add_argument("--status", default=None)
    list_command.set_defaults(handler=_list)

    show = commands.add_parser("show", help="Show a task with its runs")
    show.add_argument("task_id")
    show.set_defaults(handler=_show)

    for action in CONTROL_ACTIONS:
        control = commands.add_parser(action, help=f"{action.capitalize()} a task")
        control.add_argument("task_id")
        control.set_defaults(handler=_control, action=action)

    runs = commands.add_parser("runs", help="List runs")
    runs.add_argument("--task", dest="task_id", default=None)
    runs.set_defaults(handler=_runs)

    emit = commands.add_parser("emit", help="Emit an event that triggers `on <event>` tasks")
    emit.add_argument("event")
    emit.add_argument("--data", default="{}", help="JSON object attached to the event")
    emit.set_defaults(handler=_emit)


def _add_prompt(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prompt")
    group.add_argument(
        "--prompt-file", help="Read the prompt from a UTF-8 file, or '-' for stdin"
    )


def _config(args: argparse.Namespace) -> RunConfig:
    return load_run_config(
        workspace_root=args.workspace_root,
        agent_alias=args.agent_alias,
        max_steps=args.max_steps,
    )


def _store(config: RunConfig) -> SchedulerStore:
    return SchedulerStore(scheduler_db_path(config.pygent_root))


def _prompt(args: argparse.Namespace) -> str:
    if args.prompt is not None:
        return str(args.prompt)
    if args.prompt_file == "-":
        return sys.stdin.read()
    with open(args.prompt_file, encoding="utf-8") as handle:
        return handle.read()


def _run(args: argparse.Namespace) -> dict[str, Any]:
    daemon = build_daemon(_config(args))
    if args.once:
        return daemon.run_once()
    daemon.run_forever()
    return {"stopped": True}


def _init_keys(args: argparse.Namespace) -> dict[str, Any]:
    created = init_keys(_config(args).user_pygent_root, force=args.force)
    return {source: str(path) for source, path in created.items()}


def _add(args: argparse.Namespace) -> dict[str, Any]:
    config = _config(args)
    return submit_user(
        config,
        kind="task",
        payload=task_payload(
            name=args.name,
            schedule=args.schedule,
            prompt=_prompt(args),
            instructions=args.instructions,
            session=args.session,
            priority=args.priority,
            delivery=args.delivery,
            default_session=config.scheduler.session,
        ),
    )


def _list(args: argparse.Namespace) -> dict[str, Any]:
    store = _store(_config(args))
    return {"tasks": [task.to_dict() for task in store.list_tasks(status=args.status)]}


def _show(args: argparse.Namespace) -> dict[str, Any]:
    store = _store(_config(args))
    task = store.task(args.task_id)
    if task is None:
        raise KeyError(f"unknown task {args.task_id!r}")
    return {
        "task": task.to_dict(),
        "runs": [run.to_dict() for run in store.list_runs(task_id=task.id)],
    }


def _control(args: argparse.Namespace) -> dict[str, Any]:
    return submit_user(
        _config(args),
        kind="control",
        payload={"action": args.action, "task_id": args.task_id},
    )


def _runs(args: argparse.Namespace) -> dict[str, Any]:
    store = _store(_config(args))
    return {"runs": [run.to_dict() for run in store.list_runs(task_id=args.task_id)]}


def _emit(args: argparse.Namespace) -> dict[str, Any]:
    data = json.loads(args.data)
    if not isinstance(data, dict):
        raise ValueError("--data must be a JSON object")
    return submit_user(
        _config(args), kind="event", payload={"event": args.event, "data": data}
    )


__all__ = [
    "build_daemon",
    "lara_task_runner",
    "register_scheduler_parser",
    "submit_user",
    "task_payload",
]
