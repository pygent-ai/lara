"""Tests for the speak tool contract (D-RT10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lara.runtime.realtime.speak import (
    SPEAK_TOOL_SPEC,
    SpeakArgsError,
    execute_speak,
    parse_speak_args,
    resolve_speak_attachments,
)


class RecordingRenderer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Path, ...]]] = []

    def render(self, text: str, attachments: tuple[Path, ...]) -> None:
        self.calls.append((text, tuple(attachments)))


def test_spec_is_external_and_channel_agnostic() -> None:
    assert SPEAK_TOOL_SPEC.definition.name == "speak"
    assert SPEAK_TOOL_SPEC.side_effect.name == "EXTERNAL"
    properties = SPEAK_TOOL_SPEC.definition.parameters["properties"]
    assert set(properties) == {"text", "attachments", "say_nothing"}


def test_parse_plain_text() -> None:
    args = parse_speak_args({"text": "你好"})
    assert args.text == "你好"
    assert args.attachments == ()
    assert args.say_nothing is False


def test_parse_text_with_attachments() -> None:
    args = parse_speak_args({"text": "看这个", "attachments": ["report.md"]})
    assert args.text == "看这个"
    assert args.attachments == ("report.md",)


def test_parse_attachments_only() -> None:
    args = parse_speak_args({"attachments": ["a.md", "b.md"]})
    assert args.text is None
    assert args.attachments == ("a.md", "b.md")


def test_parse_blank_text_counts_as_missing() -> None:
    args = parse_speak_args({"text": "   ", "attachments": ["a.md"]})
    assert args.text is None
    assert args.attachments == ("a.md",)


def test_parse_say_nothing_alone() -> None:
    args = parse_speak_args({"say_nothing": True})
    assert args.say_nothing is True
    assert args.text is None
    assert args.attachments == ()


def test_parse_say_nothing_excludes_text() -> None:
    with pytest.raises(SpeakArgsError, match="excludes"):
        parse_speak_args({"say_nothing": True, "text": "其实我想说"})


def test_parse_empty_args_rejected() -> None:
    with pytest.raises(SpeakArgsError, match="requires text"):
        parse_speak_args({})


def test_parse_non_string_text_rejected() -> None:
    with pytest.raises(SpeakArgsError, match="string"):
        parse_speak_args({"text": 42})


def test_parse_attachment_items_must_be_strings() -> None:
    with pytest.raises(SpeakArgsError, match="non-empty strings"):
        parse_speak_args({"attachments": [1]})


def test_resolve_attachments_happy_path(tmp_path: Path) -> None:
    (tmp_path / "report.md").write_text("data", encoding="utf-8")
    resolved = resolve_speak_attachments(tmp_path, ["report.md"])
    assert resolved == (tmp_path / "report.md",)


def test_resolve_attachments_refuses_traversal(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="escapes the workspace"):
        resolve_speak_attachments(tmp_path, ["../outside.txt"])


def test_resolve_attachments_refuses_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        resolve_speak_attachments(tmp_path, ["ghost.md"])


def test_execute_speak_delivers_text() -> None:
    renderer = RecordingRenderer()
    args = parse_speak_args({"text": "干完了"})
    result = execute_speak(args, renderer=renderer)
    assert result == "delivered"
    assert renderer.calls == [("干完了", ())]


def test_execute_speak_silent_skips_renderer() -> None:
    renderer = RecordingRenderer()
    args = parse_speak_args({"say_nothing": True})
    result = execute_speak(args, renderer=renderer)
    assert result == "acknowledged (silent)"
    assert renderer.calls == []


def test_execute_speak_resolves_attachments(tmp_path: Path) -> None:
    (tmp_path / "out.md").write_text("x", encoding="utf-8")
    renderer = RecordingRenderer()
    args = parse_speak_args({"text": "附件在", "attachments": ["out.md"]})
    result = execute_speak(args, renderer=renderer, workspace_root=tmp_path)
    assert result == "delivered"
    assert renderer.calls == [("附件在", (tmp_path / "out.md",))]


def test_execute_speak_attachments_need_workspace(tmp_path: Path) -> None:
    args = parse_speak_args({"attachments": ["out.md"]})
    with pytest.raises(SpeakArgsError, match="workspace_root"):
        execute_speak(args, renderer=RecordingRenderer())
