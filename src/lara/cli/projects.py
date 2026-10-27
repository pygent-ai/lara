from __future__ import annotations

import argparse
from typing import Any

from lara.config import load_run_config
from lara.sessions import SessionDirectory, user_lara_root_for


def register_projects_parser(subparsers: Any) -> None:
    projects = subparsers.add_parser(
        "projects", help="Inspect projects registered in the Lara directory"
    )
    commands = projects.add_subparsers(dest="projects_command", required=True)

    list_command = commands.add_parser(
        "list", help="List projects and their registered sessions"
    )
    list_command.set_defaults(handler=_projects_list)


def _projects_list(args: argparse.Namespace) -> dict[str, Any]:
    config = load_run_config(workspace_root=args.workspace_root)
    directory = SessionDirectory(user_lara_root_for(config.lara_root))
    return {"projects": directory.list_projects()}


__all__ = ["register_projects_parser"]
