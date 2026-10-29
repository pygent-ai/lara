from __future__ import annotations

import os
from pathlib import Path

import pytest

from lara.core.paths import project_lara_root, user_lara_root


@pytest.fixture(autouse=True)
def lara_project_data_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep project data (sessions, collaboration stores, index) out of ~/.lara.

    Tests that manage their own fake user home keep working untouched; only
    workspaces still tied to the real default home are relocated into the
    test's tmp tree.
    """
    real_home = os.path.normcase(str(user_lara_root()))

    def redirected(
        workspace_root: str | Path, user_root: str | Path | None = None
    ) -> Path:
        managed_by_test = (
            user_root is not None
            and os.path.normcase(str(Path(user_root).resolve())) != real_home
        )
        if not managed_by_test and str(workspace_root).startswith(str(tmp_path)):
            return project_lara_root(workspace_root, tmp_path / "lara-home")
        return project_lara_root(workspace_root, user_root)

    monkeypatch.setattr("lara.schema.models.project_lara_root", redirected)


@pytest.fixture(autouse=True)
def lara_dispatch_store_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the user-level dispatch store out of ~/.lara.

    App tests exercise the real lifespan, which starts the dispatch scheduler;
    against the real store it would materialize due tasks and claim queued
    work belonging to production. Tests that patch Path.home themselves keep
    working: their store path no longer matches the real one, so the
    redirect passes through.
    """
    from lara.dispatch import DispatchStore as real_store

    real_dispatch_store = user_lara_root() / "dispatch-v1.sqlite3"
    test_home = tmp_path / "lara-home"

    def redirected(path: str | Path, *, legacy_path: str | Path | None = None):
        if Path(path).resolve() == real_dispatch_store.resolve():
            return real_store(
                test_home / "dispatch-v1.sqlite3", legacy_path=legacy_path
            )
        return real_store(path, legacy_path=legacy_path)

    monkeypatch.setattr("lara.dispatch.DispatchStore", redirected)
