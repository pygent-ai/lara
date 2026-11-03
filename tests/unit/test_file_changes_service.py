from pathlib import Path

import pytest

from lara_api.services.file_changes import session_file_changes, turn_patch


def _write_log(session_dir: Path, records: list[dict]) -> None:
    logs = session_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(__import__("json").dumps(record) for record in records)
    (logs / "diff_events.jsonl").write_text(lines + "\n", encoding="utf-8")


def _record(**overrides: object) -> dict:
    record = {
        "event_id": "evt-1",
        "case_run_id": "run-1",
        "turn_id": "turn-1",
        "diff_id": "diff_" + "a" * 32,
        "path": "E:/proj/src/app.py",
        "relative_path": "src/app.py",
        "change_type": "modified",
        "patch_available": True,
        "patch_path": None,
        "additions": 3,
        "deletions": 1,
        "created_at": "2026-11-03T08:00:00Z",
    }
    record.update(overrides)
    return record


def test_groups_records_by_turn_and_merges_repeated_edits(tmp_path) -> None:
    diff_a = "diff_" + "a" * 32
    diff_b = "diff_" + "b" * 32
    _write_log(
        tmp_path,
        [
            _record(diff_id=diff_a, additions=3, deletions=1, created_at="2026-11-03T08:00:00Z"),
            _record(diff_id=diff_b, additions=2, deletions=4, created_at="2026-11-03T08:01:00Z"),
            _record(
                case_run_id="run-1",
                turn_id="turn-2",
                diff_id="diff_" + "c" * 32,
                relative_path="src/other.py",
                additions=5,
                deletions=0,
                created_at="2026-11-03T08:02:00Z",
            ),
        ],
    )

    payload = session_file_changes(tmp_path)

    assert payload["turns_total"] == 2
    assert payload["turns_truncated"] is False
    first, second = payload["turns"]
    assert (first["turn_id"], first["case_run_id"]) == ("turn-1", "run-1")
    assert first["change_count"] == 2
    assert first["file_count"] == 1
    assert first["total_additions"] == 5
    assert first["total_deletions"] == 5
    assert first["files"][0]["path"] == "src/app.py"
    # The later record owns the diff link and label for the merged file row.
    assert first["files"][0]["diff_id"] == diff_b
    assert second["files"][0]["path"] == "src/other.py"


def test_limit_keeps_the_most_recent_turns(tmp_path) -> None:
    _write_log(
        tmp_path,
        [
            _record(turn_id="turn-1", created_at="2026-11-03T08:00:00Z"),
            _record(turn_id="turn-2", created_at="2026-11-03T08:01:00Z"),
            _record(turn_id="turn-3", created_at="2026-11-03T08:02:00Z"),
        ],
    )

    payload = session_file_changes(tmp_path, limit=2)

    assert payload["turns_total"] == 3
    assert payload["turns_truncated"] is True
    assert [turn["turn_id"] for turn in payload["turns"]] == ["turn-2", "turn-3"]


def test_missing_counts_fall_back_to_parsing_the_patch(tmp_path) -> None:
    patch_path = tmp_path / "diffs" / "patches" / "old.patch"
    patch_path.parent.mkdir(parents=True)
    patch_path.write_text(
        "--- a/legacy.py\n+++ b/legacy.py\n@@ -1,2 +1,2 @@\n-old\n+new\n+kept\n context\n",
        encoding="utf-8",
    )
    _write_log(
        tmp_path,
        [
            _record(
                diff_id="diff_" + "d" * 32,
                relative_path="legacy.py",
                additions=None,
                deletions=None,
                patch_path=str(patch_path),
                created_at="2026-11-03T08:00:00Z",
            ),
        ],
    )

    payload = session_file_changes(tmp_path)

    turn = payload["turns"][0]
    assert turn["files"][0]["additions"] == 2
    assert turn["files"][0]["deletions"] == 1
    assert turn["total_additions"] == 2


def test_counts_without_any_patch_stay_null(tmp_path) -> None:
    _write_log(
        tmp_path,
        [
            _record(
                diff_id="diff_" + "e" * 32,
                additions=None,
                deletions=None,
                patch_available=False,
                patch_path=None,
            ),
        ],
    )

    turn = session_file_changes(tmp_path)["turns"][0]

    assert turn["files"][0]["additions"] is None
    assert turn["files"][0]["deletions"] is None
    assert turn["total_additions"] is None
    assert turn["total_deletions"] is None


def test_turn_patch_returns_persisted_patch_text(tmp_path) -> None:
    diff_id = "diff_" + "f" * 32
    patch_path = tmp_path / "cases" / "c" / "runs" / "run-1" / "diffs" / "patches" / f"{diff_id}.patch"
    patch_path.parent.mkdir(parents=True)
    patch_path.write_text("patch body", encoding="utf-8")
    _write_log(
        tmp_path,
        [_record(diff_id=diff_id, patch_path=str(patch_path))],
    )

    record = turn_patch(tmp_path, diff_id)

    assert record["patch_available"] is True
    assert record["patch"] == "patch body"
    assert record["relative_path"] == "src/app.py"


def test_turn_patch_rejects_paths_outside_the_session_dir(tmp_path) -> None:
    outside = tmp_path.parent / "outside.patch"
    outside.write_text("secret", encoding="utf-8")
    diff_id = "diff_" + "1" * 32
    _write_log(
        tmp_path,
        [_record(diff_id=diff_id, patch_path=str(outside))],
    )

    record = turn_patch(tmp_path, diff_id)

    assert record["patch_available"] is False
    assert record["patch"] == ""
    assert "escapes" in record["patch_unavailable_reason"]


def test_turn_patch_validates_the_diff_id_format(tmp_path) -> None:
    with pytest.raises(ValueError):
        turn_patch(tmp_path, "../escape")


def test_turn_patch_reports_unknown_diff_ids(tmp_path) -> None:
    assert turn_patch(tmp_path, "diff_" + "0" * 32) is None
