from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from lara.core.paths import project_lara_root


def default_state_path() -> Path:
    user_root = Path.home() / ".lara"
    return user_root / "gui" / "state.json"


@dataclass(slots=True)
class GuiProjectState:
    state_path: Path
    default_project_path: str | None = None
    recent_project_paths: list[str] | None = None
    collapsed_scope_ids: list[str] | None = None
    session_order: dict[str, list[str]] | None = None

    @classmethod
    def load(cls, state_path: str | Path | None = None) -> "GuiProjectState":
        path = Path(state_path or default_state_path()).expanduser().resolve()
        if not path.exists():
            return cls(state_path=path, recent_project_paths=[], collapsed_scope_ids=[])
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        recent = [_resolve_project(item) for item in data.get("recent_project_paths") or [] if str(item).strip()]
        default = data.get("default_project_path")
        collapsed = [_normalize_scope_id(item) for item in data.get("collapsed_scope_ids") or []]
        raw_session_order = data.get("session_order") or {}
        return cls(
            state_path=path,
            default_project_path=_resolve_project(default) if isinstance(default, str) and default.strip() else None,
            recent_project_paths=_unique(recent),
            collapsed_scope_ids=_unique([item for item in collapsed if item]),
            session_order=_session_order_from(raw_session_order),
        )

    @property
    def state_dir(self) -> Path:
        return self.state_path.parent

    def remember_project(self, project_path: str | Path) -> None:
        resolved = _resolve_project(project_path)
        known = [_resolve_project(item) for item in self.recent_project_paths or []]
        # Sidebar order is user-defined: opening a project keeps its position
        # and only appends projects that are not in the sidebar yet.
        if resolved not in known:
            known.append(resolved)
        self.default_project_path = resolved
        self.recent_project_paths = _unique(known)[:12]
        self.save()

    def forget_project(self, project_path: str | Path) -> bool:
        resolved = _resolve_project(project_path)
        recent = [
            _resolve_project(item)
            for item in self.recent_project_paths or []
            if _resolve_project(item) != resolved
        ]
        removed = len(recent) != len(self.recent_project_paths or [])
        if not removed:
            return False
        self.recent_project_paths = recent
        if self.default_project_path == resolved:
            self.default_project_path = recent[0] if recent else None
        scope_id = f"project:{resolved}"
        self.collapsed_scope_ids = [
            item for item in self.collapsed_scope_ids or [] if item != scope_id
        ]
        if self.session_order:
            self.session_order.pop(scope_id, None)
        self.save()
        return True

    def session_order_ids(self, scope_id: str) -> list[str]:
        return list((self.session_order or {}).get(scope_id) or [])

    def adopt_sessions(self, scope_id: str, session_ids: list[str]) -> list[str]:
        """Fold sessions that are not ordered yet into the top of the saved order."""

        orders = dict(self.session_order or {})
        known = list(orders.get(scope_id) or [])
        adopted = [item for item in session_ids if item and item not in known]
        if not adopted:
            return known
        orders[scope_id] = adopted + known
        self.session_order = orders
        self.save()
        return orders[scope_id]

    def reorder_projects(self, ordered_scope_ids: list[str]) -> None:
        known: list[str] = []
        for item in self.recent_project_paths or []:
            resolved = _resolve_project(item)
            if resolved not in known:
                known.append(resolved)
        ordered: list[str] = []
        for scope_id in ordered_scope_ids or []:
            if not scope_id.startswith("project:"):
                continue
            project = _resolve_project(scope_id.removeprefix("project:"))
            if project in known and project not in ordered:
                ordered.append(project)
        self.recent_project_paths = _unique(ordered + known)[:12]
        self.save()

    def reorder_sessions(self, scope_id: str, ordered_session_ids: list[str]) -> None:
        cleaned: list[str] = []
        for item in ordered_session_ids or []:
            session_id = str(item).strip()
            if session_id and session_id not in cleaned:
                cleaned.append(session_id)
        orders = dict(self.session_order or {})
        if cleaned:
            orders[scope_id] = cleaned
        else:
            orders.pop(scope_id, None)
        self.session_order = orders
        self.save()

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(
                {
                    "default_project_path": self.default_project_path,
                    "recent_project_paths": self.recent_project_paths or [],
                    "collapsed_scope_ids": self.collapsed_scope_ids or [],
                    "session_order": self.session_order or {},
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


@dataclass(frozen=True, slots=True)
class SessionScope:
    scope_id: str
    label: str
    tooltip: str
    workspace_root: str | None
    lara_root: str
    runtime_workspace_root: str


def build_session_scopes(state: GuiProjectState, *, active_workspace_root: str | None = None) -> list[SessionScope]:
    user_root = Path.home() / ".lara"
    conversation_root = user_root / "conversations"
    project_paths = list(state.recent_project_paths or [])
    if active_workspace_root:
        active_project = _resolve_project(active_workspace_root)
        if active_project not in project_paths:
            project_paths.insert(0, active_project)
    active_project = _resolve_project(active_workspace_root) if active_workspace_root else ""
    project_paths = [
        item
        for item in project_paths
        if item == active_project or Path(item).expanduser().exists()
    ]

    projects = [Path(item).expanduser().resolve() for item in _unique(project_paths)]
    scopes: list[SessionScope] = []
    for project in projects:
        scopes.append(
            SessionScope(
                scope_id=f"project:{project}",
                label=_project_label(project, projects),
                tooltip=str(project),
                workspace_root=str(project),
                runtime_workspace_root=str(project),
                lara_root=str(project_lara_root(project)),
            )
        )

    scopes.append(
        SessionScope(
            scope_id="conversation",
            label="Chat",
            tooltip="Conversations without a project path",
            workspace_root=None,
            runtime_workspace_root=str((conversation_root / "workspace").resolve()),
            lara_root=str(conversation_root.resolve()),
        )
    )
    return scopes


def active_project_scope_id(workspace_root: str) -> str:
    return f"project:{_resolve_project(workspace_root)}"


def _resolve_project(project_path: str | Path) -> str:
    return str(Path(project_path).expanduser().resolve())


def _normalize_scope_id(scope_id: object) -> str:
    value = str(scope_id).strip()
    if value == "conversation":
        return value
    if value.startswith("project:"):
        project = value.removeprefix("project:")
        return f"project:{_resolve_project(project)}" if project.strip() else ""
    return ""


def _session_order_from(raw: object) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        return {}
    orders: dict[str, list[str]] = {}
    for scope_id, session_ids in raw.items():
        if not isinstance(scope_id, str) or not scope_id.strip():
            continue
        if not isinstance(session_ids, list):
            continue
        cleaned = [item for item in session_ids if isinstance(item, str) and item.strip()]
        if cleaned:
            orders[scope_id] = _unique(cleaned)
    return orders


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _project_label(project: Path, all_projects: list[Path]) -> str:
    name = project.name or str(project)
    if sum(1 for item in all_projects if item.name == name) <= 1:
        return name
    parent = project.parent.name
    return f"{name} - {parent}" if parent else name
