from __future__ import annotations

import os
from pathlib import Path

import pytest

from lara.core.paths import project_pygent_root, user_pygent_root


@pytest.fixture(autouse=True)
def lara_project_data_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep project data (sessions, collaboration stores, index) out of ~/.lara.

    Tests that manage their own fake user home keep working untouched; only
    workspaces still tied to the real default home are relocated into the
    test's tmp tree.
    """
    real_home = os.path.normcase(str(user_pygent_root()))

    def redirected(
        workspace_root: str | Path, user_root: str | Path | None = None
    ) -> Path:
        managed_by_test = (
            user_root is not None
            and os.path.normcase(str(Path(user_root).resolve())) != real_home
        )
        if not managed_by_test and str(workspace_root).startswith(str(tmp_path)):
            return project_pygent_root(workspace_root, tmp_path / "lara-home")
        return project_pygent_root(workspace_root, user_root)

    # lora's RunConfig derives pygent_root inside the SDK __post_init__.
    monkeypatch.setattr(
        "pygent.agent.pygent_v1.schema.models.project_pygent_root", redirected
    )


@pytest.fixture(autouse=True)
def lara_scheduler_daemon_off_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the API lifespan from running the scheduler daemon.

    The daemon polls the workspace scheduler store and spawns `lara session
    run` children; app tests exercise the lifespan against tmp workspaces and
    must not start that loop. Tests that exercise the scheduler drive
    `SchedulerDaemon.run_once` directly.
    """
    monkeypatch.setattr(
        "lara_api.container.ApiContext.start_scheduler_daemon", lambda self: None
    )


@pytest.fixture(autouse=True)
def lara_feishu_off_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests away from the real Feishu app.

    App tests exercise the real lifespan, which would start the inbound
    bridge and arm the outbound speak channel against the production chat.
    Tests that exercise the Feishu pieces patch these references themselves.
    """
    monkeypatch.setattr("lara.runtime.service.load_feishu_channels", lambda root: ())
    monkeypatch.setattr(
        "lara_api.services.feishu_bridge.load_feishu_bridge_config", lambda root: None
    )
