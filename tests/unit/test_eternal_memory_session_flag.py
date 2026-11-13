from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from pygent.agent.pygent_v1.runtime.eternal_conversation import eternal_enabled_for
from lara.sessions import SessionManager
from lara_api.app import create_app

from tests.unit.test_model_configuration import native_runtime_config


def test_eternal_memory_requires_both_master_switch_and_session_flag(
    tmp_path: Path,
) -> None:
    config = native_runtime_config(tmp_path)
    config.eternal_conversation.enabled = True
    ref = SessionManager(config).create("chat", mode="chat", eternal_memory=True)
    session = SessionManager(config).load(ref.session_id)
    assert eternal_enabled_for(session, config)

    # The deployment master switch stays a kill switch for flagged sessions.
    config.eternal_conversation.enabled = False
    assert not eternal_enabled_for(session, config)

    config.eternal_conversation.enabled = True
    session.metadata.pop("eternal_memory")
    assert not eternal_enabled_for(session, config)


def test_session_flag_persists_and_toggles(tmp_path: Path) -> None:
    config = native_runtime_config(tmp_path)
    manager = SessionManager(config)
    ref = manager.create("chat", mode="chat")
    session = manager.load(ref.session_id)
    assert session.metadata["eternal_memory"] is False

    manager.set_eternal_memory(session.session_id, enabled=True)
    assert manager.load(session.session_id).metadata["eternal_memory"] is True

    manager.set_eternal_memory(session.session_id, enabled=False)
    assert manager.load(session.session_id).metadata["eternal_memory"] is False


def test_fork_inherits_the_eternal_memory_flag(tmp_path: Path) -> None:
    config = native_runtime_config(tmp_path)
    manager = SessionManager(config)
    flagged = manager.create("chat", mode="chat", eternal_memory=True)
    plain = manager.create("chat", mode="chat")

    forked = manager.fork(flagged.session_id, mode="chat")
    assert forked.session_id != flagged.session_id
    # The flag rides the copied session.json, and the copied memory ledger and
    # raw history come with it, so the fork continues the memory session.
    assert manager.load(forked.session_id).metadata["eternal_memory"] is True
    assert manager.load(plain.session_id).metadata["eternal_memory"] is False


@pytest.mark.asyncio
async def test_session_api_creates_and_toggles_eternal_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    app = create_app(workspace_root=str(tmp_path))
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        created = (await client.post("/sessions", json={"eternal_memory": True})).json()
        manager = app.state.api_context.manager
        assert manager.load(created["session_id"]).metadata["eternal_memory"] is True

        toggled = await client.patch(
            f"/sessions/{created['session_id']}/eternal-memory",
            json={"enabled": False},
        )
        assert toggled.status_code == 200
        assert toggled.json() == {
            "session_id": created["session_id"],
            "eternal_memory": False,
        }
        assert (
            manager.load(created["session_id"]).metadata["eternal_memory"] is False
        )

        missing = await client.patch(
            "/sessions/does-not-exist/eternal-memory",
            json={"enabled": True},
        )
        assert missing.status_code == 404
