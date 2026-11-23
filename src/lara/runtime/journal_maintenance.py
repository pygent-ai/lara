"""One-shot maintenance for the execution journal databases.

pygent 0.6.3 prunes executions older than the retention window automatically,
but a journal created before ``auto_vacuum=INCREMENTAL`` keeps every freed
page until a VACUUM rebuilds the file. This helper checkpoints the WAL,
rebuilds the database, and leaves the file with incremental auto-vacuum
enabled, so later runtime prunes return pages to the filesystem on their own.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def vacuum_journal(path: str | Path, *, busy_timeout_seconds: float = 5.0) -> dict[str, Any]:
    """Rebuild one journal DB and enable incremental auto-vacuum on it."""

    journal = Path(path)
    size_before = journal.stat().st_size
    connection = sqlite3.connect(journal, timeout=busy_timeout_seconds)
    try:
        connection.execute(f"PRAGMA busy_timeout={int(busy_timeout_seconds * 1000)}")
        # Fail fast when a live runtime still holds the write lock instead of
        # letting VACUUM starve behind the runtime's own writer.
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("COMMIT")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
        connection.execute("VACUUM")
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        page_count = connection.execute("PRAGMA page_count").fetchone()[0]
        freelist = connection.execute("PRAGMA freelist_count").fetchone()[0]
        auto_vacuum = connection.execute("PRAGMA auto_vacuum").fetchone()[0]
    finally:
        connection.close()
    size_after = journal.stat().st_size
    return {
        "path": str(journal),
        "size_before_bytes": size_before,
        "size_after_bytes": size_after,
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist,
        "auto_vacuum": auto_vacuum,
    }


def discover_journal_paths(home: Path | None = None) -> list[Path]:
    """Every project journal DB under the lara home, largest first."""

    root = home if home is not None else Path.home() / ".lara"
    pattern = "projects/*/.lora/runtime/executions-v1.sqlite3"
    return sorted(
        root.glob(pattern), key=lambda path: path.stat().st_size, reverse=True
    )


__all__ = ["discover_journal_paths", "vacuum_journal"]
