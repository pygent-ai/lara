from __future__ import annotations

import json

from lara.sessions import SessionDirectory, user_lara_root_for


def test_user_lara_root_follows_the_project_layout(tmp_path) -> None:
    project_root = tmp_path / "home" / "projects" / "abc123"
    assert user_lara_root_for(project_root) == (tmp_path / "home").resolve()
    custom_root = tmp_path / "custom" / ".lara"
    assert user_lara_root_for(custom_root) == (tmp_path / "custom").resolve()


def test_sessions_register_and_resolve(tmp_path) -> None:
    directory = SessionDirectory(tmp_path / "home")
    project_root = tmp_path / "home" / "projects" / "a"

    directory.register_session(
        session_id="chat-chat-20260101-000000-aaaaaaaa",
        lara_root=project_root,
        workspace_root=tmp_path / "workspace-a",
        case_id="chat",
        mode="chat",
    )

    located = directory.locate_session("chat-chat-20260101-000000-aaaaaaaa")
    assert located is not None
    assert located.lara_root == str(project_root.resolve())
    assert located.case_id == "chat"
    assert directory.locate_session("chat-chat-20260101-000000-bbbbbbbb") is None


def test_locate_session_adopts_legacy_sessions_from_disk(tmp_path) -> None:
    project_root = tmp_path / "home" / "projects" / "legacy"
    session_dir = project_root / "sessions" / "chat-chat-20260101-000000-cccccccc"
    session_dir.mkdir(parents=True)
    (session_dir / "session.json").write_text("{}", encoding="utf-8")
    (session_dir / "metadata.json").write_text(
        json.dumps(
            {
                "session_id": "chat-chat-20260101-000000-cccccccc",
                "case_id": "chat",
                "mode": "chat",
                "workspace_root": str(tmp_path / "workspace-legacy"),
            }
        ),
        encoding="utf-8",
    )
    directory = SessionDirectory(tmp_path / "home")

    located = directory.locate_session("chat-chat-20260101-000000-cccccccc")

    assert located is not None
    assert located.lara_root == str(project_root.resolve())
    assert located.case_id == "chat"
    assert located.mode == "chat"
    assert located.workspace_root == str((tmp_path / "workspace-legacy").resolve())
    # The adopted session is persisted for subsequent lookups.
    again = directory.locate_session("chat-chat-20260101-000000-cccccccc")
    assert again is not None


def test_items_register_and_resolve(tmp_path) -> None:
    directory = SessionDirectory(tmp_path / "home")
    project_root = tmp_path / "home" / "projects" / "a"

    directory.register_item(
        item_id="msg-abcdef",
        kind="message",
        lara_root=project_root,
        source_session_id="chat-chat-20260101-000000-aaaaaaaa",
        target_session_id="chat-chat-20260101-000000-bbbbbbbb",
    )

    located = directory.locate_item("msg-abcdef")
    assert located is not None
    assert located.kind == "message"
    assert located.lara_root == str(project_root.resolve())
    assert directory.locate_item("msg-missing") is None


def test_register_item_rejects_unknown_kinds(tmp_path) -> None:
    directory = SessionDirectory(tmp_path / "home")
    try:
        directory.register_item(
            item_id="msg-abcdef",
            kind="task",
            lara_root=tmp_path / "home" / "projects" / "a",
            source_session_id=None,
            target_session_id=None,
        )
    except ValueError as error:
        assert "unknown collaboration item kind" in str(error)
    else:
        raise AssertionError("register_item should reject unknown kinds")


def test_projects_list_aggregates_session_counts(tmp_path) -> None:
    directory = SessionDirectory(tmp_path / "home")
    project_a = tmp_path / "home" / "projects" / "a"
    project_b = tmp_path / "home" / "projects" / "b"

    for suffix in ("aaaaaaaa", "bbbbbbbb"):
        directory.register_session(
            session_id=f"chat-chat-20260101-000000-{suffix}",
            lara_root=project_a,
            workspace_root=tmp_path / "workspace-a",
            case_id="chat",
            mode="chat",
        )
    directory.register_session(
        session_id="chat-chat-20260101-000000-cccccccc",
        lara_root=project_b,
        workspace_root=tmp_path / "workspace-b",
        case_id="chat",
        mode="chat",
    )

    projects = directory.list_projects()
    counts = {project["lara_root"]: project["session_count"] for project in projects}
    assert counts == {
        str(project_a.resolve()): 2,
        str(project_b.resolve()): 1,
    }
