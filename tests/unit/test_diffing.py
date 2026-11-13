from types import SimpleNamespace

from pygent.agent.pygent_v1.tracing.diffing import DiffRecorder


class _Store:
    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.events = []

    def append(self, event, **kwargs):
        self.events.append((event, kwargs))


def test_external_file_effect_is_audited_without_aborting(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _Store(tmp_path / "run")
    effect = SimpleNamespace(
        type="file.write",
        path=tmp_path / "outside.txt",
        tool_call_id="tool-1",
        tool_name="bash",
    )

    DiffRecorder(store, workspace).record_effect(effect, turn_id="turn-1")

    assert len(store.events) == 1
    event, data = store.events[0]
    assert event == "diff.created"
    assert data["payload"]["external_to_workspace"] is True
    assert data["payload"]["relative_path"] is None
    assert data["payload"]["patch_available"] is False
    assert data["turn_id"] == "turn-1"


def test_recorded_patch_carries_addition_and_deletion_counts(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _Store(tmp_path / "run")
    effect = SimpleNamespace(
        type="file.edit",
        path=workspace / "module.py",
        tool_call_id="tool-2",
        tool_name="edit",
        before_exists=True,
        after_exists=True,
        before_hash="b" * 64,
        after_hash="a" * 64,
        before_content="a\nb\nc\n",
        after_content="a\nB\nc\nd\n",
        before_content_available=True,
        after_content_available=True,
        before_content_unavailable_reason=None,
        after_content_unavailable_reason=None,
    )

    DiffRecorder(store, workspace).record_effect(effect, turn_id="turn-1")

    payload = store.events[0][1]["payload"]
    assert payload["additions"] == 2  # the "+++/---" headers never count
    assert payload["deletions"] == 1
