"""Tests for envelope rendering (D-RT1) and the nudge rule."""

from lara.runtime.realtime.envelope import render_exchange_envelope
from lara.runtime.realtime.nudge import (
    DEFAULT_NUDGE_INTERVAL,
    nudge_reminder,
    should_nudge,
)


def test_envelope_wraps_both_sections() -> None:
    text = render_exchange_envelope(
        user_asked="构建改成异步了吗？", response="没有，同步落盘是 Atlas 约束。"
    )
    assert text.startswith("<system-reminder>")
    assert text.endswith("</system-reminder>")
    assert "<user-asked>\n构建改成异步了吗？\n</user-asked>" in text
    assert (
        "<your-response-in-another-session>\n没有，同步落盘是 Atlas 约束。\n"
        "</your-response-in-another-session>" in text
    )


def test_envelope_strips_surrounding_whitespace() -> None:
    text = render_exchange_envelope(user_asked="  hi  \n", response="\n hello \n")
    assert "<user-asked>\nhi\n</user-asked>" in text
    assert "<your-response-in-another-session>\nhello\n</your-response-in-another-session>" in text


def test_nudge_threshold_boundary() -> None:
    assert should_nudge(rounds_since_speak=4) is False
    assert should_nudge(rounds_since_speak=5) is True
    assert should_nudge(rounds_since_speak=9, interval=10) is False
    assert should_nudge(rounds_since_speak=10, interval=10) is True


def test_nudge_interval_must_be_positive() -> None:
    import pytest

    with pytest.raises(ValueError, match="positive"):
        should_nudge(rounds_since_speak=3, interval=0)


def test_nudge_reminder_mentions_round_count() -> None:
    text = nudge_reminder(rounds_since_speak=7)
    assert "7 tool rounds" in text
    assert "speak" in text
    assert DEFAULT_NUDGE_INTERVAL == 5
