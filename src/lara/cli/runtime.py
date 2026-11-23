"""Runtime journal maintenance commands."""

from __future__ import annotations

import argparse
from typing import Any

from lara.runtime.journal_maintenance import discover_journal_paths, vacuum_journal


def register_runtime_parser(sub: Any) -> None:
    runtime = sub.add_parser(
        "runtime", help="Execution journal maintenance for runtime workdirs"
    )
    runtime_sub = runtime.add_subparsers(dest="runtime_command", required=True)
    vacuum = runtime_sub.add_parser(
        "vacuum",
        help=(
            "Checkpoint the WAL, rebuild each journal DB, and enable incremental "
            "auto-vacuum. Stop the running lara-api first."
        ),
    )
    vacuum.add_argument(
        "--path",
        action="append",
        dest="paths",
        default=None,
        help="Journal DB path; repeatable. Defaults to every project journal under ~/.lara.",
    )
    vacuum.set_defaults(handler=_vacuum)


def _vacuum(args: argparse.Namespace) -> dict[str, Any]:
    paths = args.paths or [str(path) for path in discover_journal_paths()]
    databases: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for path in paths:
        try:
            databases.append(vacuum_journal(path))
        except Exception as exc:  # noqa: BLE001 - sweep continues on a locked DB
            errors.append({"path": path, "error": str(exc)})
    # A silent no-op is dangerous here: the caller believes space was reclaimed.
    # Fail loudly when nothing succeeded, with the actual cause visible.
    if not databases:
        detail = "; ".join(f"{e['path']}: {e['error']}" for e in errors)
        raise RuntimeError(f"vacuum reclaimed nothing: {detail}")
    return {"databases": databases, "errors": errors}


__all__ = ["register_runtime_parser"]
