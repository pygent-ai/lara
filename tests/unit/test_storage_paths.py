from pathlib import Path

import pytest

from lara.config import load_run_config
from lara.core.paths import project_lara_root
from lara.schema import RunConfig
from lara.sessions import SessionManager
from lara_api.services.project_state import GuiProjectState, build_session_scopes
from lara_api.services.session_service import SessionService


def test_user_storage_isolates_projects_and_conversations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    user_root = home / ".lara"
    user_root.mkdir(parents=True)
    example = Path(__file__).resolve().parents[2] / "user-config.yaml.example"
    (user_root / "config.yaml").write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    projects = [tmp_path / "a" / "project", tmp_path / "b" / "project"]
    managers = []
    for project in projects:
        project.mkdir(parents=True)
        config = load_run_config(workspace_root=project)
        assert config.lara_root == RunConfig(workspace_root=str(project)).lara_root
        manager = SessionManager(config)
        ref = manager.create("chat", mode="chat")
        assert manager.load(ref.session_id).workspace_root == str(project.resolve())
        assert Path(ref.session_dir).is_relative_to(home / ".lara" / "projects")
        assert Path(config.runtime_durability.history_path).parent == Path(config.lara_root) / "runtime"
        assert Path(config.runtime_capacity.coordinator_path).parent == Path(config.lara_root) / "runtime"
        assert not (project / ".lara").exists()
        managers.append(manager)

    assert managers[0].sessions_root != managers[1].sessions_root
    first_id = SessionService(managers[0]).list_chat_sessions()[0].session_id
    with pytest.raises(FileNotFoundError):
        managers[1].load(first_id)
    assert not SessionService(managers[1]).delete_session(first_id)
    assert managers[0].load(first_id)
    assert project_lara_root(projects[0] / ".." / "project") == Path(managers[0].config.lara_root)

    state = GuiProjectState(tmp_path / "state.json", recent_project_paths=[str(p) for p in projects])
    scopes = build_session_scopes(state)
    assert [scope.lara_root for scope in scopes[:2]] == [manager.config.lara_root for manager in managers]
    chat = scopes[-1]
    assert Path(chat.lara_root) == home / ".lara" / "conversations"
    chat_manager = SessionManager(RunConfig(workspace_root=chat.runtime_workspace_root, lara_root=chat.lara_root))
    assert SessionService(chat_manager).list_chat_sessions() == []
    chat_manager.create("chat", mode="chat")
    assert len(SessionService(chat_manager).list_chat_sessions()) == 1
    assert all(len(SessionService(manager).list_chat_sessions()) == 1 for manager in managers)
