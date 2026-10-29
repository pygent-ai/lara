from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from lara.config import load_run_config
from lara.dispatch import (
    DispatchService,
    DispatchStore,
    automation_payload,
    run_payload,
)


def register_automation_parser(subparsers: Any) -> None:
    automation = subparsers.add_parser(
        "automation", help="Create and manage scheduled agent tasks"
    )
    commands = automation.add_subparsers(dest="automation_command", required=True)

    create = commands.add_parser("create", help="Create a scheduled task")
    create.add_argument("--name", required=True)
    _add_prompt(create, required=True)
    _add_schedule(create, required=True)
    create.add_argument("--timezone", default=None)
    destination = create.add_mutually_exclusive_group(required=True)
    destination.add_argument("--standalone", action="store_true")
    destination.add_argument("--session", dest="target_session_id")
    create.set_defaults(handler=_create)

    list_command = commands.add_parser("list", help="List scheduled tasks")
    list_command.add_argument(
        "--status", choices=("active", "paused", "completed"), default=None
    )
    list_command.set_defaults(handler=_list)

    show = commands.add_parser("show", help="Show a scheduled task")
    show.add_argument("automation_id")
    show.set_defaults(handler=_show)

    update = commands.add_parser("update", help="Update a scheduled task")
    update.add_argument("automation_id")
    update.add_argument("--name")
    _add_prompt(update, required=False)
    _add_schedule(update, required=False)
    update.add_argument("--timezone", default=None)
    target = update.add_mutually_exclusive_group()
    target.add_argument("--standalone", action="store_true")
    target.add_argument("--session", dest="target_session_id")
    update.set_defaults(handler=_update)

    for name, handler in (("pause", _pause), ("resume", _resume), ("delete", _delete)):
        command = commands.add_parser(name, help=f"{name.title()} a scheduled task")
        command.add_argument("automation_id")
        command.set_defaults(handler=handler)

    run = commands.add_parser("run", help="Queue a scheduled task to run now")
    run.add_argument("automation_id")
    run.set_defaults(handler=_run)

    runs = commands.add_parser("runs", help="List runs for a scheduled task")
    runs.add_argument("automation_id")
    runs.add_argument("--limit", type=int, default=100)
    runs.set_defaults(handler=_runs)


def _add_prompt(parser: argparse.ArgumentParser, *, required: bool) -> None:
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--prompt")
    group.add_argument(
        "--prompt-file", help="Read the prompt from a UTF-8 file, or '-' for stdin"
    )


def _add_schedule(parser: argparse.ArgumentParser, *, required: bool) -> None:
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--at", dest="at_time", help="One-time ISO date/time")
    group.add_argument("--rrule", help="RFC 5545 recurrence rule")


def _store(args: argparse.Namespace) -> DispatchStore:
    config = load_run_config(workspace_root=args.workspace_root)
    return DispatchStore(Path(config.user_lara_root) / "dispatch-v1.sqlite3")


def _service(args: argparse.Namespace) -> DispatchService:
    return DispatchService(_store(args))


def _prompt(args: argparse.Namespace) -> str | None:
    if getattr(args, "prompt", None) is not None:
        return str(args.prompt)
    source = getattr(args, "prompt_file", None)
    if source is None:
        return None
    if source == "-":
        return sys.stdin.read()
    return Path(source).read_text(encoding="utf-8")


def _destination(args: argparse.Namespace) -> tuple[str | None, str | None]:
    if getattr(args, "standalone", False):
        return "standalone", None
    target = getattr(args, "target_session_id", None)
    if target:
        return "heartbeat", str(target)
    return None, None


def _create(args: argparse.Namespace) -> dict[str, object]:
    destination, target = _destination(args)
    assert destination is not None
    task = _service(args).create(
        name=args.name,
        prompt=_prompt(args) or "",
        workspace_root=str(Path(args.workspace_root or Path.cwd()).resolve()),
        destination=destination,
        target_session_id=target,
        timezone_name=args.timezone,
        at_time=args.at_time,
        rrule=args.rrule,
    )
    return automation_payload(task, _store(args))


def _list(args: argparse.Namespace) -> dict[str, object]:
    store = _store(args)
    return {
        "automations": [
            automation_payload(item, store) for item in store.list(status=args.status)
        ]
    }


def _show(args: argparse.Namespace) -> dict[str, object]:
    store = _store(args)
    return automation_payload(store.get(args.automation_id), store)


def _update(args: argparse.Namespace) -> dict[str, object]:
    changes: dict[str, object] = {}
    for name in ("name", "at_time", "rrule"):
        value = getattr(args, name, None)
        if value is not None:
            changes[name] = value
    timezone = getattr(args, "timezone", None)
    if timezone is not None:
        changes["timezone_name"] = timezone
    prompt = _prompt(args)
    if prompt is not None:
        changes["prompt"] = prompt
    destination, target = _destination(args)
    if destination:
        changes["destination"] = destination
        changes["target_session_id"] = target
    if not changes:
        raise ValueError("at least one update field is required")
    store = _store(args)
    task = _service(args).update(args.automation_id, **changes)
    return automation_payload(task, store)


def _pause(args: argparse.Namespace) -> dict[str, object]:
    store = _store(args)
    return automation_payload(_service(args).pause(args.automation_id), store)


def _resume(args: argparse.Namespace) -> dict[str, object]:
    store = _store(args)
    return automation_payload(_service(args).resume(args.automation_id), store)


def _delete(args: argparse.Namespace) -> dict[str, object]:
    return {"deleted": _store(args).delete(args.automation_id)}


def _run(args: argparse.Namespace) -> dict[str, object]:
    return run_payload(_store(args).request_run(args.automation_id))


def _runs(args: argparse.Namespace) -> dict[str, object]:
    return {
        "runs": [
            run_payload(item)
            for item in _store(args).list_items(args.automation_id, limit=args.limit)
        ]
    }


__all__ = ["register_automation_parser"]
