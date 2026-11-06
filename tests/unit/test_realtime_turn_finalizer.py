"""Tests for the end-of-turn bottom-line decision (D-RT11)."""

from lara.runtime.realtime.turn_finalizer import (
    TURN_FINALIZER_REMINDER,
    decide_turn_finalization,
)


def test_pure_text_ending_nudges_once() -> None:
    decision = decide_turn_finalization(
        final_text="我把导出修好了", nudge_used=False
    )
    assert decision.action == "nudge"
    assert decision.reminder == TURN_FINALIZER_REMINDER


def test_nudge_reminder_names_both_options() -> None:
    decision = decide_turn_finalization(final_text="done", nudge_used=False)
    assert "speak(text=…)" in decision.reminder
    assert "say_nothing=true" in decision.reminder


def test_reminder_fires_even_on_empty_text() -> None:
    decision = decide_turn_finalization(final_text="", nudge_used=False)
    assert decision.action == "nudge"


def test_second_pure_text_ending_after_nudge_ends_silently() -> None:
    decision = decide_turn_finalization(final_text="还是文本", nudge_used=True)
    assert decision.action == "end"
    assert decision.reminder is None


def test_decision_is_deterministic() -> None:
    a = decide_turn_finalization(final_text="x", nudge_used=False)
    b = decide_turn_finalization(final_text="x", nudge_used=False)
    assert a == b
