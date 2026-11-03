from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from lara.tracing import EventStore

_DIFF_ID_PATTERN = re.compile(r"^diff_[0-9a-f]{32}$")


def session_file_changes(session_dir: str | Path, *, limit: int | None = None) -> dict[str, Any]:
    """Aggregate persisted diff records into per-turn file change summaries.

    Reads the session-wide ``logs/diff_events.jsonl`` mirror (one row per
    recorded diff), groups rows by ``turn_id`` inside their ``case_run_id``,
    and merges repeated edits of the same file into one entry whose counts
    sum the turn's occurrences. Turns come back oldest first so clients can
    window them with ``limit``.
    """
    path = Path(session_dir) / "logs" / "diff_events.jsonl"
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for record in EventStore.iter_jsonl(path):
        _absorb_record(grouped, record)
    turns = [
        _turn_view(turn)
        for turn in sorted(grouped.values(), key=lambda turn: (turn["started_at"], turn["turn_id"]))
    ]
    turns_total = len(turns)
    if limit is not None and limit > 0:
        turns = turns[-limit:]
    return {
        "turns": turns,
        "turns_total": turns_total,
        "turns_truncated": len(turns) < turns_total,
    }


def turn_patch(session_dir: str | Path, diff_id: str) -> dict[str, Any] | None:
    """Return one diff record with its unified patch text.

    The patch path is validated against the session directory: a record is
    only readable while its patch file stays inside the session tree.
    """
    if not _DIFF_ID_PATTERN.match(str(diff_id or "")):
        raise ValueError("Unsupported diff id format")
    root = Path(session_dir).resolve()
    record = _find_diff_record(root / "logs" / "diff_events.jsonl", diff_id)
    if record is None:
        return None
    response = {
        "diff_id": diff_id,
        "relative_path": record.get("relative_path"),
        "change_type": record.get("change_type"),
        "patch_available": False,
        "patch": "",
        "patch_unavailable_reason": record.get("patch_unavailable_reason"),
    }
    patch_path = record.get("patch_path")
    if not record.get("patch_available") or not patch_path:
        if response["patch_unavailable_reason"] is None:
            response["patch_unavailable_reason"] = "patch was not persisted for this change"
        return response
    resolved = Path(str(patch_path)).resolve()
    if not resolved.is_relative_to(root):
        response["patch_unavailable_reason"] = "patch path escapes the session directory"
        return response
    try:
        response["patch"] = resolved.read_text(encoding="utf-8", errors="replace")
        response["patch_available"] = True
    except OSError as exc:
        response["patch_unavailable_reason"] = f"patch file is unreadable: {exc}"
    return response


def _absorb_record(grouped: dict[tuple[str, str], dict[str, Any]], record: dict[str, Any]) -> None:
    key = (str(record.get("case_run_id") or ""), str(record.get("turn_id") or ""))
    turn = grouped.get(key)
    if turn is None:
        turn = {
            "case_run_id": key[0],
            "turn_id": key[1],
            "started_at": str(record.get("created_at") or ""),
            "change_count": 0,
            "total_additions": 0,
            "total_additions_known": True,
            "total_deletions": 0,
            "total_deletions_known": True,
            "files": {},
        }
        grouped[key] = turn
    _absorb_file(turn, record)


def _absorb_file(turn: dict[str, Any], record: dict[str, Any]) -> None:
    path_key = str(record.get("relative_path") or record.get("path") or "")
    if not path_key:
        return
    additions, additions_known = _change_counts(record, "additions")
    deletions, deletions_known = _change_counts(record, "deletions")
    if not additions_known:
        turn["total_additions_known"] = False
    if not deletions_known:
        turn["total_deletions_known"] = False
    turn["change_count"] += 1
    turn["total_additions"] += additions
    turn["total_deletions"] += deletions
    files: dict[str, dict[str, Any]] = turn["files"]
    entry = files.get(path_key)
    if entry is None:
        files[path_key] = {
            "path": path_key,
            "change_type": str(record.get("change_type") or "modified"),
            "additions": additions,
            "deletions": deletions,
            "additions_known": additions_known,
            "deletions_known": deletions_known,
            "changes": 1,
            "diff_id": str(record.get("diff_id") or ""),
            "patch_available": bool(record.get("patch_available")),
            "created_at": str(record.get("created_at") or ""),
        }
        return
    # Later records win for the label and the diff link: they describe where
    # the file ended up after the turn, not an intermediate edit.
    entry["change_type"] = str(record.get("change_type") or entry["change_type"])
    entry["diff_id"] = str(record.get("diff_id") or entry["diff_id"])
    entry["patch_available"] = bool(record.get("patch_available")) or entry["patch_available"]
    entry["additions"] += additions
    entry["deletions"] += deletions
    entry["additions_known"] = entry["additions_known"] and additions_known
    entry["deletions_known"] = entry["deletions_known"] and deletions_known
    entry["changes"] += 1
    entry["created_at"] = str(record.get("created_at") or entry["created_at"])


def _turn_view(turn: dict[str, Any]) -> dict[str, Any]:
    files = sorted(
        turn["files"].values(),
        key=lambda entry: (entry["created_at"], entry["path"]),
    )
    return {
        "case_run_id": turn["case_run_id"],
        "turn_id": turn["turn_id"],
        "started_at": turn["started_at"],
        "change_count": turn["change_count"],
        "file_count": len(files),
        "total_additions": turn["total_additions"] if turn["total_additions_known"] else None,
        "total_deletions": turn["total_deletions"] if turn["total_deletions_known"] else None,
        "files": [
            {
                "path": entry["path"],
                "change_type": entry["change_type"],
                "additions": entry["additions"] if entry["additions_known"] else None,
                "deletions": entry["deletions"] if entry["deletions_known"] else None,
                "changes": entry["changes"],
                "diff_id": entry["diff_id"],
                "patch_available": entry["patch_available"],
            }
            for entry in files
        ],
    }


def _change_counts(record: dict[str, Any], field: str) -> tuple[int, bool]:
    """Read a persisted line count, falling back to the patch text.

    Older sessions recorded no per-side counts, so the patch file is parsed
    on read for them; new records carry ``additions``/``deletions`` directly.
    """
    value = record.get(field)
    if isinstance(value, int):
        return value, True
    patch_path = record.get("patch_path")
    if not record.get("patch_available") or not patch_path:
        return 0, False
    try:
        text = Path(str(patch_path)).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0, False
    if field == "additions":
        return _count_patch_lines(text, "+", "+++"), True
    return _count_patch_lines(text, "-", "---"), True


def _count_patch_lines(patch: str, marker: str, header: str) -> int:
    return sum(
        1
        for line in patch.splitlines()
        if line.startswith(marker) and not line.startswith(header)
    )


def _find_diff_record(path: Path, diff_id: str) -> dict[str, Any] | None:
    for record in EventStore.iter_jsonl(path):
        if str(record.get("diff_id") or "") == diff_id:
            return record
    return None


__all__ = ["session_file_changes", "turn_patch"]
