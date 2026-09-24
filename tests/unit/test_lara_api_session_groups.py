from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from lara.core.io import read_json, write_json
from lara.schema import RunConfig
from lara.sessions import SessionManager
from lara.tracing.events import EventStore
from lara_api.app import create_app
from lara_api.dependencies import ApiContext
from lara_api.services.session_service import SessionService, _first_user_message_from_events, session_service_for_scope
from tests.unit.test_session_manager import native_run_config
from tests.unit.test_model_configuration import native_runtime_config


def test_session_groups_are_partitioned_by_remembered_project(tmp_path: Path) -> None:
    from lara_api.services.session_service import session_groups_response

    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    _create_titled_chat(project_a, "Project A chat")
    _create_titled_chat(project_b, "Project B chat")

    context = ApiContext(workspace_root=str(project_a), state_path=str(tmp_path / "state.json"))
    context.remember_project(project_a)
    context.remember_project(project_b)

    response = session_groups_response(context)

    groups = {group.scope.scope_id: group for group in response.groups}
    scope_a = f"project:{project_a.resolve()}"
    scope_b = f"project:{project_b.resolve()}"
    assert response.active_scope_id == scope_a
    assert groups[scope_a].scope.label == "project-a"
    assert groups[scope_b].scope.label == "project-b"
    assert groups["conversation"].scope.label == "Chat"
    assert [record.title for record in groups[scope_a].sessions] == ["Project A chat"]
    assert [record.title for record in groups[scope_b].sessions] == ["Project B chat"]


def test_conversation_scope_can_create_and_load_a_chat(tmp_path: Path, monkeypatch) -> None:
    from lara_api.models.requests import CreateSessionRequest
    from lara_api.routers.sessions import create_session, delete_session, get_session
    from lara_api.services.session_service import session_groups_response

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr("lara_api.services.project_state.Path.home", lambda: tmp_path)
    config = RunConfig(workspace_root=str(project))
    context = ApiContext(workspace_root=str(project), state_path=str(tmp_path / "state.json"), _config=config)

    created = asyncio.run(create_session(CreateSessionRequest(scope_id="conversation"), context=context))
    loaded = get_session(created.session_id, scope_id="conversation", context=context)

    assert created.scope_id == "conversation"
    assert loaded.session.scope_id == "conversation"
    assert loaded.session.session_id == created.session_id
    groups = {group.scope.scope_id: group for group in session_groups_response(context).groups}
    assert [item.session_id for item in groups["conversation"].sessions] == [created.session_id]
    assert groups[f"project:{project.resolve()}"].sessions == []

    assert delete_session(created.session_id, scope_id="conversation", context=context).deleted is True
    groups = {group.scope.scope_id: group for group in session_groups_response(context).groups}
    assert groups["conversation"].sessions == []


def test_existing_conversation_scope_can_select_new_group_model(tmp_path: Path, monkeypatch) -> None:
    from lara_api.models.requests import CreateSessionRequest, UpdateSessionModelRequest
    from lara_api.routers.sessions import create_session, get_session, update_session_model

    monkeypatch.setattr("lara_api.services.project_state.Path.home", lambda: tmp_path)
    initial = native_runtime_config(tmp_path, group=("main",))
    context = ApiContext(
        workspace_root=str(tmp_path),
        state_path=str(tmp_path / "state.json"),
        _config=initial,
    )
    created = asyncio.run(
        create_session(CreateSessionRequest(scope_id="conversation"), context=context)
    )
    assert created.selected_model_key == "main"

    context._config = native_runtime_config(tmp_path, group=("main", "new"))
    detail = get_session(created.session_id, scope_id="conversation", context=context)
    assert [model.model_key for model in detail.selectable_models] == ["main", "new"]

    changed = asyncio.run(
        update_session_model(
            created.session_id,
            UpdateSessionModelRequest(selected_model_key="new"),
            scope_id="conversation",
            context=context,
        )
    )
    assert changed.selected_model_key == "new"
    assert session_service_for_scope(context, "conversation").manager.model_selection(
        created.session_id
    ) == ("coding", "new")


def test_create_session_chooses_fixed_group_and_returns_children(
    tmp_path: Path,
) -> None:
    from lara_api.models.requests import CreateSessionRequest
    from lara_api.routers.sessions import create_session, get_session

    config = native_run_config(tmp_path)
    context = ApiContext(
        workspace_root=str(tmp_path),
        state_path=str(tmp_path / "state.json"),
        _config=config,
    )
    created = asyncio.run(
        create_session(
            CreateSessionRequest(model_group_name="coding"), context=context
        )
    )
    detail = get_session(created.session_id, context=context)

    assert created.model_group_name == "coding"
    assert created.selected_model_key == "main"
    assert [item.model_key for item in detail.selectable_models] == [
        "main",
        "backup",
    ]


def test_idle_session_can_change_only_its_preferred_child(tmp_path: Path) -> None:
    from lara_api.models.requests import (
        CreateSessionRequest,
        UpdateSessionModelRequest,
    )
    from lara_api.routers.sessions import create_session, update_session_model

    config = native_run_config(tmp_path)
    context = ApiContext(
        workspace_root=str(tmp_path),
        state_path=str(tmp_path / "state.json"),
        _config=config,
    )
    created = asyncio.run(create_session(CreateSessionRequest(), context=context))
    changed = asyncio.run(
        update_session_model(
            created.session_id,
            UpdateSessionModelRequest(selected_model_key="backup"),
            context=context,
        )
    )
    assert changed.model_group_name == "coding"
    assert changed.selected_model_key == "backup"


def test_active_session_rejects_preferred_child_change(tmp_path: Path) -> None:
    from fastapi import HTTPException
    from lara_api.models.requests import (
        CreateSessionRequest,
        UpdateSessionModelRequest,
    )
    from lara_api.routers.sessions import create_session, update_session_model

    config = native_run_config(tmp_path)
    context = ApiContext(
        workspace_root=str(tmp_path),
        state_path=str(tmp_path / "state.json"),
        _config=config,
    )
    created = asyncio.run(create_session(CreateSessionRequest(), context=context))
    context._session_coordinator = SimpleNamespace(
        session_busy=AsyncMock(return_value=True)
    )
    with pytest.raises(HTTPException) as captured:
        asyncio.run(
            update_session_model(
                created.session_id,
                UpdateSessionModelRequest(selected_model_key="backup"),
                context=context,
            )
        )
    assert captured.value.status_code == 409


def test_update_settings_remembers_switched_workspace_for_project_list(tmp_path: Path) -> None:
    from lara_api.models.requests import UpdateSettingsRequest
    from lara_api.routers.projects import list_projects
    from lara_api.routers.settings import update_settings

    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    context = ApiContext(workspace_root=str(project_a), state_path=str(tmp_path / "state.json"))
    context.remember_project(project_a)

    asyncio.run(update_settings(UpdateSettingsRequest(workspace_root=str(project_b), max_steps=-1), context=context))

    response = list_projects(context=context)

    assert response.active.scope_id == f"project:{project_b.resolve()}"
    # Sidebar order is user-defined: switching workspaces remembers the
    # project but must not move it to the front.
    assert [project.scope_id for project in response.projects] == [
        f"project:{project_a.resolve()}",
        f"project:{project_b.resolve()}",
    ]


def test_project_list_omits_missing_recent_directories(tmp_path: Path) -> None:
    from lara_api.routers.projects import list_projects

    project = tmp_path / "project"
    missing = tmp_path / "missing"
    project.mkdir()
    context = ApiContext(workspace_root=str(project), state_path=str(tmp_path / "state.json"))
    context.project_state.recent_project_paths = [str(missing), str(project)]
    context.project_state.default_project_path = str(missing)
    context.project_state.save()

    response = list_projects(context=context)

    assert [item.scope_id for item in response.projects] == [f"project:{project.resolve()}"]


def test_non_active_project_can_be_removed_without_deleting_files(tmp_path: Path) -> None:
    from lara_api.routers.projects import delete_project, list_projects

    active = tmp_path / "active"
    removable = tmp_path / "removable"
    active.mkdir()
    removable.mkdir()
    marker = removable / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    context = ApiContext(workspace_root=str(active), state_path=str(tmp_path / "state.json"))
    context.remember_project(removable)
    context.remember_project(active)

    response = delete_project(f"project:{removable.resolve()}", context=context)

    assert response.deleted is True
    assert marker.read_text(encoding="utf-8") == "keep"
    assert [item.scope_id for item in list_projects(context=context).projects] == [
        f"project:{active.resolve()}"
    ]


def test_active_project_cannot_be_removed(tmp_path: Path) -> None:
    from fastapi import HTTPException
    from lara_api.routers.projects import delete_project

    active = tmp_path / "active"
    active.mkdir()
    context = ApiContext(workspace_root=str(active), state_path=str(tmp_path / "state.json"))
    context.remember_project(active)

    with pytest.raises(HTTPException) as exc_info:
        delete_project(f"project:{active.resolve()}", context=context)

    assert exc_info.value.status_code == 409


def test_session_groups_ignore_project_lara_yaml(tmp_path: Path) -> None:
    from lara_api.services.session_service import session_groups_response

    active_project = tmp_path / "active-project"
    invalid_project = tmp_path / "invalid-project"
    _create_titled_chat(active_project, "Active chat")
    invalid_project.mkdir()
    (invalid_project / "lara.yaml").write_text(
        "\n".join(
            [
                "agents:",
                "  - alias: dev",
                "    model_request:",
                "      routes:",
                "        - id: primary",
                "          provider: openai",
                "          model_name: test-model",
                "          base_url: https://example.test",
                "          api_key_env: TEST_KEY",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    context = ApiContext(workspace_root=str(active_project), state_path=str(tmp_path / "state.json"))
    context.remember_project(invalid_project)
    context.remember_project(active_project)

    response = session_groups_response(context)

    assert [group.scope.scope_id for group in response.groups] == [
        f"project:{invalid_project.resolve()}",
        f"project:{active_project.resolve()}",
        "conversation",
    ]


def test_session_list_uses_first_user_message_as_title_when_metadata_title_is_missing(tmp_path: Path) -> None:
    manager = SessionManager(
        RunConfig(
            workspace_root=str(tmp_path),
            lara_root=str((tmp_path / ".lara").resolve()),
        )
    )
    ref = manager.create("chat", mode="chat")
    session = manager.load(ref.session_id)
    session.history = [
        {"role": "assistant", "content": "ready"},
        {"role": "user", "content": "  Build a LoRA training script\nwith resume support  "},
    ]
    manager.save(session)

    records = SessionService(manager).list_chat_sessions()

    assert records[0].title == "Build a LoRA training script with resume support"


def test_session_list_ignores_incomplete_metadata_only_sessions(tmp_path: Path) -> None:
    manager = SessionManager(
        RunConfig(
            workspace_root=str(tmp_path),
            lara_root=str((tmp_path / ".lara").resolve()),
        )
    )
    valid = manager.create("chat", mode="chat")
    incomplete = Path(manager.sessions_root) / "chat-chat-incomplete"
    incomplete.mkdir(parents=True)
    write_json(
        incomplete / "metadata.json",
        {
            "session_id": incomplete.name,
            "case_id": "chat",
            "mode": "chat",
            "created_at": "2026-08-21T00:00:00Z",
            "updated_at": "2026-08-21T00:00:00Z",
        },
    )

    records = SessionService(manager).list_chat_sessions()

    assert [record.session_id for record in records] == [valid.session_id]


def test_title_lookup_stops_after_first_historical_user_message(tmp_path: Path) -> None:
    first = tmp_path / "cases" / "chat" / "runs" / "run-001" / "events.jsonl"
    later = tmp_path / "cases" / "chat" / "runs" / "run-002" / "events.jsonl"
    first.parent.mkdir(parents=True)
    later.parent.mkdir(parents=True)
    first.touch()
    later.touch()

    def iter_events(path: Path):
        if path == later:
            raise AssertionError("title lookup scanned a later run after finding the title")
        return iter(
            [
                {
                    "type": "conversation.user_message",
                    "payload": {"raw_content": "First historical message"},
                }
            ]
        )

    with patch.object(EventStore, "iter_jsonl", side_effect=iter_events) as mocked:
        assert _first_user_message_from_events(tmp_path) == "First historical message"

    mocked.assert_called_once_with(first)


def test_remember_project_keeps_existing_sidebar_order(tmp_path: Path) -> None:
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    context = ApiContext(workspace_root=str(project_a), state_path=str(tmp_path / "state.json"))

    context.remember_project(project_a)
    context.remember_project(project_b)
    context.remember_project(project_a)

    assert context.project_state.recent_project_paths == [
        str(project_a.resolve()),
        str(project_b.resolve()),
    ]


def test_projects_order_can_be_rearranged_and_survives_workspace_switch(tmp_path: Path) -> None:
    from lara_api.models.requests import ReorderProjectsRequest, UpdateSettingsRequest
    from lara_api.routers.projects import list_projects, reorder_projects
    from lara_api.routers.settings import update_settings

    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_c = tmp_path / "project-c"
    for project in (project_a, project_b, project_c):
        project.mkdir()
    context = ApiContext(workspace_root=str(project_a), state_path=str(tmp_path / "state.json"))
    context.remember_project(project_a)
    context.remember_project(project_b)
    context.remember_project(project_c)

    reordered = reorder_projects(
        ReorderProjectsRequest(
            ordered_scope_ids=[
                f"project:{project_c.resolve()}",
                f"project:{project_a.resolve()}",
            ]
        ),
        context=context,
    )
    assert [project.scope_id for project in reordered.projects] == [
        f"project:{project_c.resolve()}",
        f"project:{project_a.resolve()}",
        f"project:{project_b.resolve()}",
    ]

    # Switching workspaces (also triggered by selecting a session in another
    # project) must keep the user-defined sidebar order intact.
    asyncio.run(
        update_settings(
            UpdateSettingsRequest(workspace_root=str(project_b), max_steps=-1),
            context=context,
        )
    )
    assert [project.scope_id for project in list_projects(context=context).projects] == [
        f"project:{project_c.resolve()}",
        f"project:{project_a.resolve()}",
        f"project:{project_b.resolve()}",
    ]


def test_sessions_follow_drag_order_and_new_sessions_land_on_top(tmp_path: Path) -> None:
    from lara_api.models.requests import ReorderSessionsRequest
    from lara_api.routers.sessions import reorder_session_groups
    from lara_api.services.session_service import session_groups_response

    context = ApiContext(workspace_root=str(tmp_path), state_path=str(tmp_path / "state.json"))
    service = SessionService(context.manager)
    first = service.create_session().session_id
    second = service.create_session().session_id
    third = service.create_session().session_id
    scope_id = f"project:{tmp_path.resolve()}"

    groups = {group.scope.scope_id: group for group in session_groups_response(context).groups}
    assert {item.session_id for item in groups[scope_id].sessions} == {first, second, third}

    reorder_session_groups(
        ReorderSessionsRequest(
            scope_id=scope_id,
            ordered_session_ids=[first, second, third],
        ),
        context=context,
    )
    groups = {group.scope.scope_id: group for group in session_groups_response(context).groups}
    assert [item.session_id for item in groups[scope_id].sessions] == [first, second, third]

    fourth = service.create_session().session_id
    groups = {group.scope.scope_id: group for group in session_groups_response(context).groups}
    assert [item.session_id for item in groups[scope_id].sessions] == [fourth, first, second, third]


@pytest.mark.asyncio
async def test_session_groups_etag_short_circuits_unchanged_polls(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    app = create_app(workspace_root=str(tmp_path))
    context = app.state.api_context
    ref = SessionService(context.manager).create_session().session_id
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        # The first build folds new sessions into the persisted order, which
        # rotates the tag exactly once; warm up before asserting stability.
        assert (await client.get("/sessions/groups")).status_code == 200
        first = await client.get("/sessions/groups")
        assert first.status_code == 200
        etag = first.headers["ETag"]
        assert etag.startswith('"')

        cached = await client.get("/sessions/groups", headers={"If-None-Match": etag})
        assert cached.status_code == 304
        assert cached.content == b""

        metadata_path = Path(context.manager.sessions_root) / ref / "metadata.json"
        metadata = read_json(metadata_path)
        metadata["title"] = "Renamed chat"
        write_json(metadata_path, metadata)

        updated = await client.get("/sessions/groups", headers={"If-None-Match": etag})
        assert updated.status_code == 200
        assert updated.headers["ETag"] != etag
        titles = [
            item["title"]
            for group in updated.json()["groups"]
            for item in group["sessions"]
        ]
        assert "Renamed chat" in titles


@pytest.mark.asyncio
async def test_session_groups_etag_follows_gui_state_changes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    app = create_app(workspace_root=str(tmp_path))
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        first = await client.get("/sessions/groups")
        etag = first.headers["ETag"]

        SessionService(app.state.api_context.manager).create_session()

        updated = await client.get("/sessions/groups", headers={"If-None-Match": etag})
        assert updated.status_code == 200
        assert updated.headers["ETag"] != etag


def _create_titled_chat(workspace_root: Path, title: str) -> str:
    workspace_root.mkdir(parents=True, exist_ok=True)
    manager = SessionManager(
        RunConfig(
            workspace_root=str(workspace_root),
        )
    )
    ref = manager.create("chat", mode="chat")
    metadata_path = Path(ref.session_dir) / "metadata.json"
    metadata = read_json(metadata_path)
    metadata["title"] = title
    write_json(metadata_path, metadata)
    return ref.session_id
