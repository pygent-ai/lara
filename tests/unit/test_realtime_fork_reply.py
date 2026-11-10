"""Tests for fork_reply construction/extraction and the fork execution module."""

from __future__ import annotations

import pytest
from pygent import AIMessage, Context, ToolCall, ToolDefinition, UserMessage

from pygent.lara.runtime.realtime.fork_agent import (
    FORK_RESULT_KIND,
    ForkReplyModule,
    outcome_from_result,
)
from pygent.lara.runtime.realtime.fork_reply import (
    build_fork_context,
    extract_speak_args,
)


def _speak_definition() -> ToolDefinition:
    return ToolDefinition(
        name="speak",
        description="Say something to the user.",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )


def _other_definition() -> ToolDefinition:
    return ToolDefinition(
        name="read",
        description="Read a file.",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
    )


def _speak_call(call_id: str, arguments: dict) -> ToolCall:
    return ToolCall(call_id=call_id, name="speak", arguments=arguments)


def _read_call(call_id: str) -> ToolCall:
    return ToolCall(call_id=call_id, name="read", arguments={"path": "a.md"})


def test_build_fork_context_copies_request_verbatim() -> None:
    messages = (UserMessage(content="把报表导出修一下"),)
    tools = (_speak_definition(), _other_definition())
    context = build_fork_context(
        system_prompt="You are Lara.", messages=messages, tools=tools
    )
    assert context.system_prompt == "You are Lara."
    assert context.messages == messages
    assert context.tools == tools


def test_extract_speak_args_variants() -> None:
    spoke = AIMessage(
        content="", tool_calls=(_speak_call("c1", {"text": "你好，马上处理。"}),)
    )
    assert extract_speak_args(spoke) is not None
    assert extract_speak_args(spoke).text == "你好，马上处理。"

    mixed = AIMessage(
        content="",
        tool_calls=(_read_call("c3"), _speak_call("c4", {"text": "ok"})),
    )
    assert extract_speak_args(mixed).text == "ok"

    assert extract_speak_args(AIMessage(content="纯文本")) is None


class _FakeLayer:
    """Stands in for the managed ModelCallLayer inside module.forward."""

    def __init__(self, answer: AIMessage) -> None:
        self._answer = answer
        self.calls: list[tuple] = []

    async def __call__(self, message, context):
        self.calls.append((message, context))
        return self._answer, context


def _module(answer: AIMessage) -> tuple[ForkReplyModule, _FakeLayer]:
    module = ForkReplyModule(
        config=object(),
        model_group_name="default",
        llm=object(),
        tools=(_speak_definition(),),
        retry_policy=object(),
    )
    layer = _FakeLayer(answer)
    module.model = layer  # allowed before the first invoke/bind
    return module, layer


async def test_fork_module_returns_speak_outcome() -> None:
    module, layer = _module(
        AIMessage(content="", tool_calls=(_speak_call("c1", {"text": "在做了"}),))
    )
    context = Context(system_prompt="s", messages=(), tools=(_speak_definition(),))
    result, _ = await module.forward(
        UserMessage(content="进度如何"), context
    )
    assert result.kind == FORK_RESULT_KIND
    assert result.data["via"] == "speak"
    assert dict(result.data["speak_args"]) == {
        "text": "在做了",
        "attachments": (),
        "say_nothing": False,
    }
    # The user message reaches the managed layer separately from the prefix.
    assert layer.calls[0][0].content == "进度如何"

    outcome = outcome_from_result(result)
    assert outcome.via == "speak"
    assert outcome.speak_args is not None
    assert outcome.speak_args.text == "在做了"


async def test_fork_module_stray_text_becomes_text_outcome() -> None:
    module, _ = _module(AIMessage(content="未走工具的文本"))
    result, _ = await module.forward(
        UserMessage(content="hi"), Context(system_prompt="s")
    )
    assert result.data["via"] == "text"
    assert result.data["text"] == "未走工具的文本"
    outcome = outcome_from_result(result)
    assert outcome.via == "text"
    assert outcome.text == "未走工具的文本"


async def test_fork_module_say_nothing_roundtrip() -> None:
    module, _ = _module(
        AIMessage(content="", tool_calls=(_speak_call("c2", {"say_nothing": True}),))
    )
    result, _ = await module.forward(UserMessage(content="hi"), Context())
    outcome = outcome_from_result(result)
    assert outcome.speak_args is not None
    assert outcome.speak_args.say_nothing is True


def test_outcome_from_result_rejects_foreign_kind() -> None:
    with pytest.raises(ValueError):
        outcome_from_result(AIMessage(content="x", kind="lara.chat.result"))
