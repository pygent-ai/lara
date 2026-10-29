from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from lara.core.io import validate_path_id

from .models import (
    DispatchDestination,
    DispatchItem,
    DispatchItemKind,
    DispatchItemState,
    DispatchTask,
    DispatchTaskStatus,
    next_occurrence,
    normalize_rrule,
    normalize_timezone,
    parse_at_time,
    utc_now,
)

LEGACY_MIGRATION_MARKER = "legacy_automations_migrated_at"

_TERMINAL_STATES = (
    DispatchItemState.PASSED,
    DispatchItemState.FAILED,
    DispatchItemState.ERROR,
    DispatchItemState.SKIPPED,
    DispatchItemState.CANCELLED,
)


def _fingerprint(**values: Any) -> str:
    payload = json.dumps(
        values, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class DispatchStore:
    """User-level store for dispatch task definitions and their items.

    Holds both scheduled automation turns (task_id set) and immediately
    submitted start/fork items (task_id NULL) so one scheduler can drive
    every dispatch execution.
    """

    def __init__(self, path: str | Path, *, legacy_path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.legacy_path = (
            Path(legacy_path).expanduser().resolve()
            if legacy_path is not None
            else self.path.parent / "automations-v1.sqlite3"
        )
        self._initialize()
        self.migrate_legacy_automations()

    # ------------------------------------------------------------------ tasks

    def create(
        self,
        *,
        name: str,
        prompt: str,
        workspace_root: str,
        destination: str,
        target_session_id: str | None = None,
        timezone_name: str | None = None,
        at_time: str | None = None,
        rrule: str | None = None,
    ) -> DispatchTask:
        clean_name = _required(name, "name")
        clean_prompt = _required(prompt, "prompt")
        workspace = str(Path(_required(workspace_root, "workspace_root")).resolve())
        destination_value = DispatchDestination(destination).value
        if destination_value == DispatchDestination.HEARTBEAT.value:
            target_session_id = _required(target_session_id or "", "target_session_id")
        else:
            target_session_id = None
        zone = normalize_timezone(timezone_name)
        if bool(at_time) == bool(rrule):
            raise ValueError("exactly one of at_time or rrule is required")
        now = utc_now()
        clean_at = parse_at_time(at_time, zone) if at_time else None
        clean_rrule = normalize_rrule(rrule) if rrule else None
        self._require_future_occurrence(
            at_time=clean_at,
            rrule=clean_rrule,
            timezone_name=zone,
            dtstart=now,
        )
        task_id = f"auto-{uuid.uuid4().hex}"
        with closing(self._connect()) as connection:
            connection.execute(
                """INSERT INTO dispatch_tasks(
                       task_id, name, prompt, workspace_root, destination,
                       target_session_id, timezone_name, at_time, rrule,
                       status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    clean_name,
                    clean_prompt,
                    workspace,
                    destination_value,
                    target_session_id,
                    zone,
                    clean_at,
                    clean_rrule,
                    DispatchTaskStatus.ACTIVE.value,
                    now,
                    now,
                ),
            )
            connection.commit()
        return self.get(task_id)

    def list(self, *, status: str | None = None) -> list[DispatchTask]:
        query = "SELECT * FROM dispatch_tasks"
        values: tuple[object, ...] = ()
        if status:
            query += " WHERE status = ?"
            values = (DispatchTaskStatus(status).value,)
        query += " ORDER BY updated_at DESC"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, values).fetchall()
        return [_task(row) for row in rows]

    def get(self, task_id: str) -> DispatchTask:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM dispatch_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"dispatch task not found: {task_id}")
        return _task(row)

    def update(self, task_id: str, **changes: Any) -> DispatchTask:
        current = self.get(task_id)
        allowed = {
            "name",
            "prompt",
            "workspace_root",
            "destination",
            "target_session_id",
            "timezone_name",
            "at_time",
            "rrule",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported dispatch task fields: {sorted(unknown)}")
        values = current.to_dict()
        values.update({key: value for key, value in changes.items() if value is not None})
        zone = normalize_timezone(str(values["timezone_name"]))
        destination = DispatchDestination(str(values["destination"])).value
        target = values.get("target_session_id")
        if destination == DispatchDestination.HEARTBEAT.value:
            target = _required(str(target or ""), "target_session_id")
        else:
            target = None
        at_value = values.get("at_time")
        rule_value = values.get("rrule")
        if "at_time" in changes and changes["at_time"] is not None:
            rule_value = None
        if "rrule" in changes and changes["rrule"] is not None:
            at_value = None
        if bool(at_value) == bool(rule_value):
            raise ValueError("exactly one of at_time or rrule is required")
        now = utc_now()
        clean_at = parse_at_time(str(at_value), zone) if at_value else None
        clean_rule = normalize_rrule(str(rule_value)) if rule_value else None
        self._require_future_occurrence(
            at_time=clean_at,
            rrule=clean_rule,
            timezone_name=zone,
            dtstart=current.created_at,
        )
        with closing(self._connect()) as connection:
            connection.execute(
                """UPDATE dispatch_tasks SET name=?, prompt=?, workspace_root=?,
                       destination=?, target_session_id=?, timezone_name=?, at_time=?,
                       rrule=?, status=?, updated_at=?
                   WHERE task_id=?""",
                (
                    _required(str(values["name"]), "name"),
                    _required(str(values["prompt"]), "prompt"),
                    str(Path(str(values["workspace_root"])).resolve()),
                    destination,
                    target,
                    zone,
                    clean_at,
                    clean_rule,
                    DispatchTaskStatus.ACTIVE.value,
                    now,
                    task_id,
                ),
            )
            connection.commit()
        return self.get(task_id)

    def set_status(self, task_id: str, status: str) -> DispatchTask:
        value = DispatchTaskStatus(status).value
        if value == DispatchTaskStatus.ACTIVE.value:
            current = self.get(task_id)
            if not current.rrule and current.at_time and current.at_time <= utc_now():
                raise ValueError("a completed one-time automation cannot be resumed")
        with closing(self._connect()) as connection:
            connection.execute(
                "UPDATE dispatch_tasks SET status=?, updated_at=? WHERE task_id=?",
                (value, utc_now(), task_id),
            )
            connection.commit()
        return self.get(task_id)

    def delete(self, task_id: str) -> bool:
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "DELETE FROM dispatch_tasks WHERE task_id=?", (task_id,)
            )
            connection.commit()
        return cursor.rowcount > 0

    def pause_after_failure(self, task_id: str) -> None:
        self.set_status(task_id, DispatchTaskStatus.PAUSED.value)

    # ------------------------------------------------------------------ items

    def submit_item(
        self,
        *,
        kind: str,
        workspace_root: str,
        message: str,
        agent_alias: str = "",
        case_id: str = "automation",
        task_id: str | None = None,
        target_session_id: str | None = None,
        source_session_id: str | None = None,
        model_group: str | None = None,
        submission_id: str | None = None,
        fingerprint: str | None = None,
    ) -> DispatchItem:
        kind_value = DispatchItemKind(kind).value
        clean_message = _required(message, "message")
        workspace = str(Path(_required(workspace_root, "workspace_root")).resolve())
        clean_case_id = _required(case_id, "case_id")
        if model_group is not None and not model_group.strip():
            raise ValueError("model_group must be non-empty when provided")
        if submission_id is not None and not submission_id.strip():
            raise ValueError("submission_id must be non-empty")
        if target_session_id is not None:
            validate_path_id(target_session_id, "target_session_id")
        if source_session_id is not None:
            validate_path_id(source_session_id, "source_session_id")
        if submission_id is not None and fingerprint is None:
            fingerprint = _fingerprint(
                kind=kind_value,
                workspace_root=workspace,
                message=clean_message,
                agent_alias=agent_alias,
                case_id=clean_case_id,
                task_id=task_id,
                target_session_id=target_session_id,
                source_session_id=source_session_id,
                model_group=model_group,
            )
        item_id = f"op-{uuid.uuid4().hex}"
        now = utc_now()
        with closing(self._connect()) as connection:
            try:
                connection.execute(
                    """INSERT INTO dispatch_items(
                           item_id, task_id, kind, workspace_root, target_session_id,
                           source_session_id, agent_alias, case_id, model_group,
                           message, submission_id, fingerprint, state, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
                    (
                        item_id,
                        task_id,
                        kind_value,
                        workspace,
                        target_session_id,
                        source_session_id,
                        agent_alias,
                        clean_case_id,
                        model_group,
                        clean_message,
                        submission_id,
                        fingerprint,
                        now,
                        now,
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError:
                pass
            if submission_id is not None:
                row = connection.execute(
                    "SELECT * FROM dispatch_items WHERE submission_id = ?",
                    (submission_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM dispatch_items WHERE item_id = ?", (item_id,)
                ).fetchone()
        if row is None:
            raise RuntimeError("dispatch item submission was not persisted")
        if fingerprint is not None and row["fingerprint"] is not None:
            if str(row["fingerprint"]) != fingerprint:
                raise ValueError("submission_id was already used for a different request")
        return _item(row)

    def request_run(self, task_id: str) -> DispatchItem:
        task = self.get(task_id)
        return self.submit_item(
            kind=DispatchItemKind.TURN.value,
            workspace_root=task.workspace_root,
            message=task.prompt,
            task_id=task.task_id,
            target_session_id=task.target_session_id,
        )

    def get_item(self, item_id: str) -> DispatchItem:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM dispatch_items WHERE item_id = ?", (item_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"dispatch item not found: {item_id}")
        return _item(row)

    def list_items(self, task_id: str, *, limit: int = 100) -> list[DispatchItem]:
        self.get(task_id)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT * FROM dispatch_items WHERE task_id=?
                   ORDER BY created_at DESC LIMIT ?""",
                (task_id, max(1, min(limit, 500))),
            ).fetchall()
        return [_item(row) for row in rows]

    def list_items_for_session(
        self, session_id: str, *, limit: int = 100
    ) -> list[DispatchItem]:
        """Items submitted by or targeting one session, newest first."""
        validate_path_id(session_id, "session_id")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT * FROM dispatch_items
                   WHERE source_session_id=? OR target_session_id=?
                   ORDER BY rowid DESC LIMIT ?""",
                (session_id, session_id, max(1, min(limit, 200))),
            ).fetchall()
        return [_item(row) for row in rows]

    def claim_items(
        self, *, owner_id: str, limit: int = 4, lease_seconds: float
    ) -> list[DispatchItem]:
        """Claim queued items under a renewable lease.

        Expired claimed/running items return to queued first. A queued item is
        skipped while another item of the same task still runs, and start/fork
        items for one target session are claimed in submission order.
        """
        if limit <= 0:
            return []
        now_epoch = time.time()
        now = utc_now()
        claimed: list[DispatchItem] = []
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE dispatch_items SET state='queued', owner_id=NULL,
                       lease_until_epoch=NULL, updated_at=?
                   WHERE state IN ('claimed','running')
                     AND lease_until_epoch IS NOT NULL AND lease_until_epoch <= ?""",
                (now, now_epoch),
            )
            rows = connection.execute(
                """SELECT rowid AS item_sequence, * FROM dispatch_items
                   WHERE state='queued' ORDER BY rowid LIMIT ?""",
                (limit,),
            ).fetchall()
            for row in rows:
                item_id = str(row["item_id"])
                task_id = row["task_id"]
                target_session_id = row["target_session_id"]
                if task_id is not None:
                    active = connection.execute(
                        """SELECT 1 FROM dispatch_items WHERE task_id=? AND item_id<>?
                           AND state IN ('claimed','running') LIMIT 1""",
                        (task_id, item_id),
                    ).fetchone()
                    if active is not None:
                        connection.execute(
                            """UPDATE dispatch_items SET state='skipped',
                                   error='previous automation run is still active',
                                   owner_id=NULL, lease_until_epoch=NULL, updated_at=?
                               WHERE item_id=? AND state='queued'""",
                            (now, item_id),
                        )
                        continue
                if target_session_id is not None:
                    earlier = connection.execute(
                        """SELECT 1 FROM dispatch_items
                           WHERE target_session_id=? AND rowid<?
                             AND state NOT IN ('passed','failed','error','skipped','cancelled')
                           LIMIT 1""",
                        (target_session_id, int(row["item_sequence"])),
                    ).fetchone()
                    blocked = connection.execute(
                        """SELECT 1 FROM dispatch_items
                           WHERE target_session_id=? AND item_id<>?
                             AND state IN ('claimed','running') LIMIT 1""",
                        (target_session_id, item_id),
                    ).fetchone()
                    if earlier is not None or blocked is not None:
                        continue
                cursor = connection.execute(
                    """UPDATE dispatch_items SET state='claimed', owner_id=?,
                           lease_until_epoch=?, updated_at=?
                       WHERE item_id=? AND state='queued'""",
                    (owner_id, now_epoch + lease_seconds, now, item_id),
                )
                if cursor.rowcount:
                    claimed.append(_item(connection.execute(
                        "SELECT * FROM dispatch_items WHERE item_id=?", (item_id,)
                    ).fetchone()))
            connection.commit()
        return claimed

    def mark_running(
        self,
        item_id: str,
        *,
        owner_id: str,
        execution_id: str | None = None,
        case_run_id: str | None = None,
        lease_seconds: float,
    ) -> DispatchItem:
        self._owned_update(
            item_id,
            owner_id=owner_id,
            values={
                "state": DispatchItemState.RUNNING.value,
                "execution_id": execution_id,
                "case_run_id": case_run_id,
                "lease_until_epoch": time.time() + lease_seconds,
            },
        )
        return self.get_item(item_id)

    def heartbeat(
        self, item_id: str, *, owner_id: str, lease_seconds: float
    ) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """UPDATE dispatch_items SET lease_until_epoch=?, updated_at=?
                   WHERE item_id=? AND owner_id=? AND state IN ('claimed','running')""",
                (time.time() + lease_seconds, utc_now(), item_id, owner_id),
            )
            return cursor.rowcount == 1

    def attach_target(
        self, item_id: str, target_session_id: str, *, owner_id: str
    ) -> DispatchItem:
        validate_path_id(target_session_id, "target_session_id")
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """UPDATE dispatch_items SET target_session_id=?, updated_at=?
                   WHERE item_id=? AND owner_id=? AND state IN ('claimed','running')""",
                (target_session_id, utc_now(), item_id, owner_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("dispatch item cannot accept a target session")
        return self.get_item(item_id)

    def finish_item(
        self,
        item_id: str,
        *,
        owner_id: str,
        state: str,
        final_answer: str = "",
        error: str | None = None,
        execution_id: str | None = None,
        case_run_id: str | None = None,
    ) -> DispatchItem:
        state_value = DispatchItemState(state).value
        if state_value not in {item.value for item in _TERMINAL_STATES}:
            raise ValueError("finish requires a terminal dispatch item state")
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """UPDATE dispatch_items SET state=?, final_answer=?, error=?,
                       execution_id=?, case_run_id=?, owner_id=NULL,
                       lease_until_epoch=NULL, updated_at=?
                   WHERE item_id=? AND owner_id=? AND state IN ('claimed','running')""",
                (
                    state_value,
                    final_answer,
                    error,
                    execution_id,
                    case_run_id,
                    utc_now(),
                    item_id,
                    owner_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("dispatch item lease was lost")
        return self.get_item(item_id)

    def record_task_run(
        self, task_id: str, item_id: str, *, completed: bool = False
    ) -> DispatchTask:
        """Track the latest scheduler-materialized item for due-time chains."""
        completed_value = (
            DispatchTaskStatus.COMPLETED.value if completed else None
        )
        with closing(self._connect()) as connection:
            connection.execute(
                """UPDATE dispatch_tasks SET last_run_id=?,
                       status=COALESCE(?, status), updated_at=? WHERE task_id=?""",
                (item_id, completed_value, utc_now(), task_id),
            )
            connection.commit()
        return self.get(task_id)

    def next_run_at(self, task: DispatchTask) -> str | None:
        """Due time for clients; computed because the schema stores no column."""
        if task.rrule:
            after = task.created_at
            if task.last_run_id:
                try:
                    after = self.get_item(task.last_run_id).created_at
                except KeyError:
                    pass
            return next_occurrence(
                rrule=task.rrule,
                timezone_name=task.timezone_name,
                dtstart=task.created_at,
                after=after,
            )
        return None if task.last_run_id else task.at_time

    def last_run_at(self, task: DispatchTask) -> str | None:
        if not task.last_run_id:
            return None
        try:
            return self.get_item(task.last_run_id).created_at
        except KeyError:
            return None

    # -------------------------------------------------------------- migration

    def migrate_legacy_automations(self) -> bool:
        """Import the legacy user-level automation store exactly once.

        Task and run IDs are preserved; runs become kind='turn' items. A legacy
        run still marked running belongs to the old scheduler's lease, so it is
        requeued with no owner instead of freezing its task forever.
        """
        if not self.legacy_path.exists():
            return False
        with closing(self._connect()) as connection:
            marker = connection.execute(
                "SELECT value FROM dispatch_meta WHERE key=?", (LEGACY_MIGRATION_MARKER,)
            ).fetchone()
        if marker is not None:
            return False
        tasks, runs = _read_legacy_automations(self.legacy_path)
        last_run_ids = {
            run["automation_id"]: run["run_id"] for run in runs
        }
        task_by_id = {task["automation_id"]: task for task in tasks}
        with closing(self._connect()) as connection, connection:
            for task in tasks:
                connection.execute(
                    """INSERT OR IGNORE INTO dispatch_tasks(
                           task_id, name, prompt, workspace_root, destination,
                           target_session_id, timezone_name, at_time, rrule,
                           status, created_at, updated_at, last_run_id
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        task["automation_id"],
                        task["name"],
                        task["prompt"],
                        task["workspace_root"],
                        task["destination"],
                        task["target_session_id"],
                        task["timezone"],
                        task["at_time"],
                        task["rrule"],
                        task["status"],
                        task["created_at"],
                        task["updated_at"],
                        last_run_ids.get(task["automation_id"]),
                    ),
                )
            for run in runs:
                task = task_by_id[run["automation_id"]]
                state = run["status"]
                if state == DispatchItemState.RUNNING.value:
                    state = DispatchItemState.QUEUED.value
                connection.execute(
                    """INSERT OR IGNORE INTO dispatch_items(
                           item_id, task_id, kind, workspace_root, target_session_id,
                           source_session_id, agent_alias, case_id, model_group,
                           message, submission_id, fingerprint, state, owner_id,
                           lease_until_epoch, execution_id, case_run_id,
                           final_answer, error, created_at, updated_at
                       ) VALUES (?, ?, 'turn', ?, ?, NULL, '', 'automation', NULL,
                                 ?, NULL, NULL, ?, NULL, NULL, ?, ?, ?, ?, ?, ?)""",
                    (
                        run["run_id"],
                        run["automation_id"],
                        task["workspace_root"],
                        task["target_session_id"],
                        task["prompt"],
                        state,
                        run["execution_id"],
                        run["case_run_id"],
                        run["final_answer"] or "",
                        run["error"],
                        run["created_at"],
                        run["finished_at"] or run["created_at"],
                    ),
                )
            connection.execute(
                "INSERT INTO dispatch_meta(key, value) VALUES (?, ?)",
                (LEGACY_MIGRATION_MARKER, utc_now()),
            )
        return True

    # ---------------------------------------------------------------- internal

    @staticmethod
    def _require_future_occurrence(
        *,
        at_time: str | None,
        rrule: str | None,
        timezone_name: str,
        dtstart: str,
    ) -> None:
        next_run = at_time or next_occurrence(
            rrule=rrule or "", timezone_name=timezone_name, dtstart=dtstart, after=utc_now()
        )
        if next_run is None:
            raise ValueError("schedule has no future occurrence")

    def _owned_update(
        self, item_id: str, *, owner_id: str, values: dict[str, Any]
    ) -> None:
        assignments = ", ".join(f"{name} = ?" for name in values)
        parameters = [*values.values(), utc_now(), item_id, owner_id]
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                f"UPDATE dispatch_items SET {assignments}, updated_at = ? WHERE item_id = ? AND owner_id = ?",  # noqa: S608
                parameters,
            )
            if cursor.rowcount != 1:
                raise RuntimeError("dispatch item lease was lost")

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS dispatch_tasks(
                    task_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    workspace_root TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    target_session_id TEXT,
                    timezone_name TEXT,
                    at_time TEXT,
                    rrule TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_run_id TEXT
                );
                CREATE TABLE IF NOT EXISTS dispatch_items(
                    item_id TEXT PRIMARY KEY,
                    task_id TEXT REFERENCES dispatch_tasks(task_id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    workspace_root TEXT NOT NULL,
                    target_session_id TEXT,
                    source_session_id TEXT,
                    agent_alias TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    model_group TEXT,
                    message TEXT NOT NULL,
                    submission_id TEXT UNIQUE,
                    fingerprint TEXT,
                    state TEXT NOT NULL,
                    owner_id TEXT,
                    lease_until_epoch REAL,
                    execution_id TEXT,
                    case_run_id TEXT,
                    final_answer TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dispatch_meta(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_dispatch_items_task
                    ON dispatch_items(task_id, created_at);
                CREATE INDEX IF NOT EXISTS ix_dispatch_items_state
                    ON dispatch_items(state);
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def _read_legacy_automations(
    legacy_path: Path,
) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    with closing(sqlite3.connect(legacy_path, timeout=10)) as connection:
        connection.row_factory = sqlite3.Row
        tasks = connection.execute("SELECT * FROM automations ORDER BY rowid").fetchall()
        runs = connection.execute("SELECT * FROM automation_runs ORDER BY rowid").fetchall()
    return tasks, runs


def _task(row: sqlite3.Row) -> DispatchTask:
    return DispatchTask(**{field: row[field] for field in DispatchTask.__dataclass_fields__})


def _item(row: sqlite3.Row) -> DispatchItem:
    values = {field: row[field] for field in DispatchItem.__dataclass_fields__}
    values["state"] = DispatchItemState(str(values["state"]))
    values["lease_until_epoch"] = (
        None if values["lease_until_epoch"] is None else float(values["lease_until_epoch"])
    )
    values["final_answer"] = str(values["final_answer"] or "")
    return DispatchItem(**values)


def _required(value: str, name: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{name} must be non-empty")
    return clean


__all__ = ["DispatchStore", "LEGACY_MIGRATION_MARKER"]
