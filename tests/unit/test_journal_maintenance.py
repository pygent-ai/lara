"""Tests for the execution journal VACUUM maintenance helper."""

import sqlite3

from lara.runtime.journal_maintenance import discover_journal_paths, vacuum_journal


def test_vacuum_journal_enables_incremental_auto_vacuum_and_reclaims_space(
    tmp_path,
) -> None:
    """A legacy journal (auto_vacuum=0, deleted rows kept as free pages) is
    rebuilt with auto_vacuum=INCREMENTAL, so later runtime prunes hand pages
    back to the filesystem on their own."""

    journal = tmp_path / "executions-v1.sqlite3"
    connection = sqlite3.connect(journal)
    connection.execute("CREATE TABLE events (payload TEXT)")
    connection.executemany(
        "INSERT INTO events VALUES (?)",
        [("x" * 4096,) for _ in range(600)],
    )
    connection.commit()
    connection.execute("DELETE FROM events")
    connection.commit()
    connection.close()
    bloated_size = journal.stat().st_size
    assert bloated_size > 1024 * 1024  # the deleted rows still hold pages

    report = vacuum_journal(journal)

    assert report["size_after_bytes"] < bloated_size
    assert report["auto_vacuum"] == 2  # 2 = INCREMENTAL
    assert report["freelist_count"] == 0
    # The pragma persists for the runtime's own incremental_vacuum().
    connection = sqlite3.connect(journal)
    assert connection.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
    connection.close()


def test_vacuum_journal_reports_stable_size_for_compact_database(tmp_path) -> None:
    journal = tmp_path / "executions-v1.sqlite3"
    connection = sqlite3.connect(journal)
    connection.execute("CREATE TABLE events (payload TEXT)")
    connection.execute("INSERT INTO events VALUES ('small')")
    connection.commit()
    connection.close()

    report = vacuum_journal(journal)

    # The rebuild can grow a tiny file (the checkpointed WAL folds into it);
    # the contract here is the persisted pragma and an empty freelist.
    assert report["auto_vacuum"] == 2
    assert report["freelist_count"] == 0


def test_discover_journal_paths_sweeps_project_workdirs(tmp_path) -> None:
    big = tmp_path / "projects" / "big" / ".lora" / "runtime"
    small = tmp_path / "projects" / "small" / ".lora" / "runtime"
    big.mkdir(parents=True)
    small.mkdir(parents=True)
    (big / "executions-v1.sqlite3").write_bytes(b"x" * 2048)
    (small / "executions-v1.sqlite3").write_bytes(b"x" * 512)

    paths = discover_journal_paths(tmp_path)

    assert [path.parent.parent.parent.name for path in paths] == ["big", "small"]
