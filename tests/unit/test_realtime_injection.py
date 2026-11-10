"""Tests for the realtime nudge injection module (Phase 2 wiring)."""

from __future__ import annotations

import pytest
from pygent import AIMessage, ToolCall, ToolMessage, ToolResult

from pygent.lara.runtime.context import LaraContext
from lara.runtime.realtime.injection import RealtimeNudgeModule
from lara.runtime.realtime.nudge import NUDGE_REMINDER


def _tool_message(content: str = "result text") -> ToolMessage:
    return ToolMessage(
        content=content,
        results=(ToolResult(call_id="c1", name="read", status="succeeded"),),
    )


def _assistant(*calls: ToolCall) -> AIMessage:
    return AIMessage(content="", tool_calls=tuple(calls))


def _read_call() -> ToolCall:
    return ToolCall(call_id="c1", name="read", arguments={"path": "a.md"})


def _speak_call() -> ToolCall:
    return ToolCall(call_id="c9", name="speak", arguments={"text": "进展顺利"})


async def test_silent_rounds_accumulate_and_fire_at_interval() -> None:
    module = RealtimeNudgeModule(interval=3)
    context = LaraContext(messages=(_assistant(_read_call()),))
    message = _tool_message()

    for _ in range(2):
        out, _ = await module.invoke(message, context)
        assert out.content == "result text"

    out, _ = await module.invoke(message, context)
    assert NUDGE_REMINDER.format(rounds=3) in out.content
    assert out.content.startswith("result text")


async def test_nudge_fires_periodically_not_every_round() -> None:
    module = RealtimeNudgeModule(interval=3)
    context = LaraContext(messages=(_assistant(_read_call()),))
    message = _tool_message()

    contents = []
    for _ in range(6):
        out, _ = await module.invoke(message, context)
        contents.append(out.content)
    assert contents[2].count(NUDGE_REMINDER.format(rounds=3)) == 1
    assert contents[3] == "result text"
    assert contents[4] == "result text"
    assert NUDGE_REMINDER.format(rounds=6) in contents[5]


async def test_speak_call_resets_counter() -> None:
    module = RealtimeNudgeModule(interval=2)
    silent = LaraContext(messages=(_assistant(_read_call()),))
    spoke = LaraContext(messages=(_assistant(_read_call(), _speak_call()),))
    message = _tool_message()

    _, _ = await module.invoke(message, silent)
    out, _ = await module.invoke(message, spoke)  # speak resets to 0
    assert out.content == "result text"
    out, _ = await module.invoke(message, silent)  # one silent round since speak
    assert out.content == "result text"
    out, _ = await module.invoke(message, silent)  # second silent round fires
    assert NUDGE_REMINDER.format(rounds=2) in out.content


async def test_nudge_becomes_content_when_tool_result_is_empty() -> None:
    module = RealtimeNudgeModule(interval=1)
    context = LaraContext(messages=(_assistant(_read_call()),))
    out, _ = await module.invoke(_tool_message(content=""), context)
    assert out.content == NUDGE_REMINDER.format(rounds=1)


async def test_non_speak_calls_do_not_reset_counter() -> None:
    module = RealtimeNudgeModule(interval=2)
    context = LaraContext(messages=(_assistant(_read_call()),))
    message = _tool_message()
    _, _ = await module.invoke(message, context)
    out, _ = await module.invoke(message, context)
    assert NUDGE_REMINDER.format(rounds=2) in out.content


async def test_no_assistant_message_counts_as_silent() -> None:
    module = RealtimeNudgeModule(interval=1)
    context = LaraContext(messages=())
    out, _ = await module.invoke(_tool_message(), context)
    assert NUDGE_REMINDER.format(rounds=1) in out.content


def test_invalid_interval_rejected() -> None:
    with pytest.raises(ValueError):
        RealtimeNudgeModule(interval=0)
