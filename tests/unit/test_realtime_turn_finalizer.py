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


# --- coordinator trigger (D-RT11 wiring) ---

from types import SimpleNamespace

from lara.orchestration.session_execution import SessionExecutionCoordinator
from lara.runtime.realtime.turn_finalizer import TURN_FINALIZER_REMINDER


def _finalizer_turn(*, enabled=True, history=None, turn_id="t1"):
    return SimpleNamespace(
        runtime_service=SimpleNamespace(
            config=SimpleNamespace(
                realtime_interaction=SimpleNamespace(enabled=enabled)
            )
        ),
        command=SimpleNamespace(session_id="s1", case_id="chat", turn_id=turn_id),
        run_ref=SimpleNamespace(case_run_id="cr1"),
        manager=SimpleNamespace(
            load=lambda sid: SimpleNamespace(
                history=history if history is not None else [{"role": "assistant", "content": "纯文本草稿"}]
            )
        ),
        lease=object(),
    )


async def test_finalizer_queues_followup_for_pure_text_ending() -> None:
    coordinator = SessionExecutionCoordinator()
    submitted: list = []

    async def fake_submit(*, lease, command):
        submitted.append(command)

    coordinator.submit_turn = fake_submit  # type: ignore[method-assign]
    await coordinator._maybe_start_finalizer(_finalizer_turn())
    assert len(submitted) == 1
    command = submitted[0]
    assert command.message_kind == "lara.realtime.finalizer"
    assert command.turn_id == "t1:finalize"
    assert command.interactive_approvals is False
    assert "<system-reminder>" in command.message
    assert TURN_FINALIZER_REMINDER in command.message


async def test_finalizer_fires_once_per_turn() -> None:
    coordinator = SessionExecutionCoordinator()
    submitted: list = []

    async def fake_submit(*, lease, command):
        submitted.append(command)

    coordinator.submit_turn = fake_submit  # type: ignore[method-assign]
    turn = _finalizer_turn()
    await coordinator._maybe_start_finalizer(turn)
    await coordinator._maybe_start_finalizer(turn)
    assert len(submitted) == 1


async def test_finalizer_skips_tool_call_endings() -> None:
    coordinator = SessionExecutionCoordinator()
    submitted: list = []

    async def fake_submit(*, lease, command):
        submitted.append(command)

    coordinator.submit_turn = fake_submit  # type: ignore[method-assign]
    turn = _finalizer_turn(
        history=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"call_id": "c", "name": "read"}],
            }
        ]
    )
    await coordinator._maybe_start_finalizer(turn)
    assert submitted == []


async def test_finalizer_skips_when_disabled() -> None:
    coordinator = SessionExecutionCoordinator()
    submitted: list = []

    async def fake_submit(*, lease, command):
        submitted.append(command)

    coordinator.submit_turn = fake_submit  # type: ignore[method-assign]
    await coordinator._maybe_start_finalizer(_finalizer_turn(enabled=False))
    assert submitted == []


async def test_finalizer_never_recurses_into_finalizer_turns() -> None:
    coordinator = SessionExecutionCoordinator()
    submitted: list = []

    async def fake_submit(*, lease, command):
        submitted.append(command)

    coordinator.submit_turn = fake_submit  # type: ignore[method-assign]
    await coordinator._maybe_start_finalizer(
        _finalizer_turn(turn_id="t1:finalize")
    )
    assert submitted == []
