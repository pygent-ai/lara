from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from lara.core.io import (
    append_jsonl,
    read_json,
    utc_now,
    validate_path_id,
    write_json,
    write_json_atomic,
)
from lara.core.process import current_owner
from pygent.agent.pygent_v1.core.redaction import redact_secrets
from lara.schema import (
    AgentSession,
    CaseRunRef,
    CaseRunStatus,
    RunConfig,
    SessionRef,
    SessionSpec,
)
from lara.sessions.directory import SessionDirectory, user_pygent_root_for

from pygent.agent.pygent_v1.sessions.manager import (
    PYGENT_SAYS_SESSION_MODE,
    SessionManager as _SDKSessionManager,
    realtime_enabled_for,
)

# Realtime interaction is exclusive to the dedicated "Lara说" session.
LARA_SAYS_SESSION_MODE = PYGENT_SAYS_SESSION_MODE


class SessionManager(_SDKSessionManager):
    """SDK session storage + lora product layer (directory index, run queries)."""

    def __init__(self, config: RunConfig):
        super().__init__(config)
        self._directory = SessionDirectory(user_pygent_root_for(self.pygent_root))

    def create(
        self,
        case_id: str,
        mode: str = "e2e",
        *,
        model_group_name: str | None = None,
        selected_model_key: str | None = None,
        eternal_memory: bool = False,
    ) -> SessionRef:
        validate_path_id(case_id, "case_id")
        selected_group, selected_model = self._selection_for_group(model_group_name)
        # Validate before any directory is created so a bad model key cannot
        # leave an orphan session behind.
        if selected_model_key:
            self._require_group_model(selected_group, selected_model_key)
            selected_model = selected_model_key
        session_id = (
            LARA_SAYS_SESSION_MODE
            if mode == LARA_SAYS_SESSION_MODE
            else self._new_session_id(case_id, mode)
        )
        session_dir = self.sessions_root / session_id
        session_dir.mkdir(parents=True, exist_ok=False)
        for child in ("context", "state", "logs", "cases"):
            (session_dir / child).mkdir(parents=True, exist_ok=True)

        now = utc_now()
        session = AgentSession(
            session_id=session_id,
            workspace_root=str(self.workspace_root),
            session_dir=str(session_dir),
            created_at=now,
            updated_at=now,
            model_group_name=selected_group,
            selected_model_key=selected_model,
            metadata={
                "active_case_id": case_id,
                "mode": mode,
                "eternal_memory": eternal_memory,
            },
        )
        write_json_atomic(session_dir / "session.json", session.to_dict())
        write_json(
            session_dir / "metadata.json",
            {
                "session_id": session_id,
                "case_id": case_id,
                "mode": mode,
                "created_at": now,
                "updated_at": now,
                "workspace_root": str(self.workspace_root),
                "model_group_name": selected_group,
                "selected_model_key": selected_model,
            },
        )
        self._register_in_directory(session_id=session_id, case_id=case_id, mode=mode)
        return SessionRef(
            session_id=session_id,
            session_dir=str(session_dir),
            workspace_root=str(self.workspace_root),
        )
    def load(self, session_id: str) -> AgentSession:
        session_dir = self._session_dir(session_id)
        path = session_dir / "session.json"
        if not path.exists():
            raise FileNotFoundError(f"Session {session_id!r} does not exist")
        data = read_json(path)
        data["session_dir"] = str(session_dir)
        session = AgentSession.from_dict(data)
        self._resolve_missing_selection(session)
        session = self._apply_history_checkpoints(session)
        self._register_in_directory(
            session_id=session_id,
            case_id=str(session.metadata.get("active_case_id") or ""),
            mode=str(session.metadata.get("mode") or ""),
        )
        return session

    def _register_in_directory(
        self, *, session_id: str, case_id: str, mode: str
    ) -> None:
        """Keep the user-level directory addressable across projects."""
        self._directory.register_session(
            session_id=session_id,
            pygent_root=str(self.pygent_root),
            workspace_root=str(self.workspace_root),
            case_id=case_id,
            mode=mode,
        )
    def find_case_run(self, session_id: str, case_run_id: str) -> CaseRunRef:
        session_dir = self._session_dir(session_id)
        matches = list((session_dir / "cases").glob(f"*/runs/{case_run_id}"))
        if not matches:
            raise FileNotFoundError(
                f"Case run {case_run_id!r} does not exist under session {session_id!r}"
            )
        if len(matches) > 1:
            raise ValueError(
                f"Case run {case_run_id!r} is ambiguous under session {session_id!r}"
            )
        run_dir = matches[0]
        metadata = read_json(run_dir / "run_metadata.json")
        return CaseRunRef(
            session_id=session_id,
            case_id=metadata["case_id"],
            case_run_id=case_run_id,
            run_dir=str(run_dir),
        )
    def history_before_count(self, session: AgentSession, sequence: int) -> int:
        """Count checkpoint-owned messages strictly above the given sequence.

        Reports how many rows sit older than the returned history page so the
        client knows whether another page can be fetched.
        """
        database_path = self._checkpoint_database_path(session.session_id)
        if not database_path.exists():
            return 0
        with closing(self._checkpoint_connection(database_path)) as connection:
            row = connection.execute(
                """SELECT COUNT(*)
                   FROM conversation_checkpoints
                   WHERE include_in_session = 1 AND sequence < ? AND sequence <= ?""",
                (
                    int(sequence),
                    int(session.metadata.get("history_checkpoint_seq") or 0),
                ),
            ).fetchone()
        return int(row[0] if row else 0)
    def history_count(self, session: AgentSession) -> int:
        """Count checkpoint-owned messages without decoding their JSON."""
        database_path = self._checkpoint_database_path(session.session_id)
        if not database_path.exists():
            return 0
        with closing(self._checkpoint_connection(database_path)) as connection:
            row = connection.execute(
                """SELECT COUNT(*)
                   FROM conversation_checkpoints
                   WHERE include_in_session = 1 AND sequence <= ?""",
                (int(session.metadata.get("history_checkpoint_seq") or 0),),
            ).fetchone()
        return int(row[0] if row else 0)
    def history_with_run_timing(
        self,
        session: AgentSession,
        *,
        limit: int | None = None,
        before_sequence: int | None = None,
    ) -> list[dict[str, Any]]:
        """Project checkpoint-owned history with its run timing and timestamps.

        Apply the display window in SQLite instead of materializing every
        checkpoint and slicing it in the API layer.  Long-running chats can
        contain megabytes of old tool output, so decoding the full history on
        every session switch or freshness check is needlessly expensive.

        The window is tail-anchored: ``limit`` returns the newest rows, and
        ``before_sequence`` pages strictly older rows (exclusive cursor) so a
        client can walk back towards the first message page by page.
        """
        database_path = self._checkpoint_database_path(session.session_id)
        if not database_path.exists():
            return []
        with closing(self._checkpoint_connection(database_path)) as connection:
            checkpoint_sequence = int(
                session.metadata.get("history_checkpoint_seq") or 0
            )
            bounds = "include_in_session = 1 AND sequence <= ?"
            parameters: list[Any] = [checkpoint_sequence]
            if before_sequence is not None:
                bounds += " AND sequence < ?"
                parameters.append(int(before_sequence))
            if limit is not None and limit > 0:
                rows = connection.execute(
                    f"""SELECT sequence, case_id, case_run_id, message_json, created_at
                       FROM (
                           SELECT sequence, case_id, case_run_id, message_json, created_at
                           FROM conversation_checkpoints
                           WHERE {bounds}
                           ORDER BY sequence DESC
                           LIMIT ?
                       )
                       ORDER BY sequence""",
                    (*parameters, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"""SELECT sequence, case_id, case_run_id, message_json, created_at
                       FROM conversation_checkpoints
                       WHERE {bounds}
                       ORDER BY sequence""",
                    parameters,
                ).fetchall()
        history: list[dict[str, Any]] = []
        timings: dict[str, dict[str, Any]] = {}
        for sequence, case_id, run_id, message_json, created_at in rows:
            if run_id not in timings:
                ref = CaseRunRef(
                    session_id=session.session_id,
                    case_id=case_id,
                    case_run_id=run_id,
                    run_dir=str(
                        Path(session.session_dir) / "cases" / case_id / "runs" / run_id
                    ),
                )
                timings[run_id] = self.run_timing(ref)
            message = json.loads(message_json)
            message["checkpoint_sequence"] = int(sequence)
            message["run_timing"] = timings[run_id]
            message["created_at"] = created_at
            history.append(message)
        return history
    def load_or_create(self, spec: SessionSpec) -> AgentSession:
        if spec.mode in {"resume", "shared"}:
            if not spec.session_id:
                raise ValueError(f"session_id is required for {spec.mode} mode")
            return self.load(spec.session_id)
        if spec.mode == "fork":
            if not spec.source_session_id:
                raise ValueError("source_session_id is required for fork mode")
            return self.load(self.fork(spec.source_session_id).session_id)
        return self.load(self.create(spec.case_id).session_id)
    def load_run_config(
        self,
        ref: CaseRunRef,
        *,
        credential_source: RunConfig | None = None,
    ) -> RunConfig:
        """Load persisted native run settings; credentials resolve at client build."""

        config = RunConfig.from_dict(read_json(Path(ref.run_dir) / "run_config.json"))
        del credential_source
        return config
    def rename_model_key(self, previous: str, current: str) -> int:
        """Point persisted session selections at a renamed model key.

        Sessions persist only the model key, so a configuration rename must
        migrate the stored selections; going through ``load``/``save`` keeps
        the mirrored ``metadata.json`` fields consistent with ``session.json``.
        """

        if previous == current:
            return 0
        renamed = 0
        for metadata_path in sorted(self.sessions_root.glob("*/metadata.json")):
            if not (metadata_path.parent / "session.json").is_file():
                continue
            metadata = read_json(metadata_path)
            if metadata.get("selected_model_key") != previous:
                continue
            session = self.load(str(metadata["session_id"]))
            session.selected_model_key = current
            self.save(session)
            renamed += 1
        return renamed
    def run_owner(self, ref: CaseRunRef) -> dict[str, Any] | None:
        """Return the process stamp recorded when the run started, if any."""

        metadata = read_json(Path(ref.run_dir) / "run_metadata.json", default={})
        owner = metadata.get("owner")
        return owner if isinstance(owner, dict) else None
    def run_timing(self, ref: CaseRunRef) -> dict[str, Any]:
        metadata = read_json(Path(ref.run_dir) / "run_metadata.json", default={})
        return {
            "case_run_id": ref.case_run_id,
            "started_at": metadata.get("started_at"),
            "finished_at": metadata.get("finished_at"),
            "status": metadata.get("status"),
        }
    def set_selected_model(
        self,
        session_id: str,
        model_key: str,
        *,
        model_group_name: str | None = None,
    ) -> AgentSession:
        session = self.load(session_id)
        target_group = model_group_name or session.model_group_name
        self._require_group_model(target_group, model_key)
        if model_group_name and model_group_name != session.model_group_name:
            session.model_group_name = target_group
        session.selected_model_key = model_key
        self.save(session)
        return session


def _slug(value: str) -> str:
    clean = "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")
    return clean or "case"


def _clean_title(value: str) -> str:
    return " ".join(value.split())
