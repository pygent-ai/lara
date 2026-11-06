"""Tests for fork_reply: request construction, extraction, delivery paths."""

from __future__ import annotations



from pygent import AIMessage, GenerationConfig, ToolCall, ToolDefinition
from pygent.llm import ModelProviderResponse

from lara.runtime.realtime.fork_reply import (
    FORCED_SPEAK_GENERATION,
    build_fork_context,
    extract_speak_args,
    run_fork_reply,
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


def _assistant(text: str = "") -> AIMessage:
    return AIMessage(content=text)


class FakeExecution:
    def __init__(self, response: ModelProviderResponse) -> None:
        self._response = response

    async def result(self) -> ModelProviderResponse:
        return self._response


class FakeInvoker:
    def __init__(self, response: ModelProviderResponse) -> None:
        self._response = response
        self.calls: list[dict] = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return FakeExecution(self._response)


def _response(tool_calls=(), text: str = "") -> ModelProviderResponse:
    # Real 0.3.32 response type (pygent.llm export; requires the env upgrade).
    return ModelProviderResponse(message=AIMessage(content=text, tool_calls=tool_calls))


def _speak_call(call_id: str, arguments: dict | str) -> ToolCall:
    return ToolCall(call_id=call_id, name="speak", arguments=arguments)


def _read_call(call_id: str) -> ToolCall:
    return ToolCall(call_id=call_id, name="read", arguments={"path": "a.md"})


def _prefix_messages() -> tuple:
    from pygent import UserMessage

    return (UserMessage(content="把报表导出修一下"),)


def test_build_fork_context_copies_request_verbatim() -> None:
    messages = _prefix_messages()
    tools = (_speak_definition(), _other_definition())
    context = build_fork_context(
        system_prompt="You are Lara.", messages=messages, tools=tools
    )
    assert context.system_prompt == "You are Lara."
    assert context.messages == messages
    assert context.tools == tools


def test_forced_path_extracts_speak_args() -> None:
    message = AIMessage(
        content="", tool_calls=(_speak_call("c1", {"text": "你好，马上处理。"}),)
    )
    speak_args = extract_speak_args(message)
    assert speak_args is not None
    assert speak_args.text == "你好，马上处理。"


def test_extract_ignores_non_speak_calls() -> None:
    message = AIMessage(
        content="",
        tool_calls=(
            _read_call("c3"),
            _speak_call("c4", {"text": "ok"}),
        ),
    )
    speak_args = extract_speak_args(message)
    assert speak_args is not None
    assert speak_args.text == "ok"


def test_extract_returns_none_without_speak() -> None:
    message = AIMessage(content="纯文本", tool_calls=())
    assert extract_speak_args(message) is None


async def test_run_fork_reply_forced_path() -> None:
    invoker = FakeInvoker(
        _response(
            tool_calls=(
                {"call_id": "c5", "name": "speak", "arguments": {"text": "在做了"}},
            )
        )
    )
    context = build_fork_context(
        system_prompt="s", messages=_prefix_messages(), tools=(_speak_definition(),)
    )
    from pygent import UserMessage

    outcome = await run_fork_reply(
        invoker=invoker,
        model_group=object(),
        retry_policy=object(),
        context=context,
        user_message=UserMessage(content="进度如何"),
    )
    assert outcome.via == "speak"
    assert outcome.speak_args is not None
    assert outcome.speak_args.text == "在做了"
    # request shape: prefix copied verbatim, user message appended separately
    call = invoker.calls[0]
    assert call["message"].content == "进度如何"
    assert len(call["context"].messages) == 1
    assert call["context"].messages[0].content == "把报表导出修一下"
    assert call["generation"].tool_choice == "speak"
    assert [t.name for t in call["tools"]] == ["speak"]


async def test_run_fork_reply_forced_stray_text_falls_back() -> None:
    invoker = FakeInvoker(_response(text="未走工具的文本"))
    context = build_fork_context(system_prompt="s", messages=(), tools=(_speak_definition(),))
    from pygent import UserMessage

    outcome = await run_fork_reply(
        invoker=invoker,
        model_group=object(),
        retry_policy=object(),
        context=context,
        user_message=UserMessage(content="hi"),
    )
    assert outcome.via == "text"
    assert outcome.text == "未走工具的文本"


async def test_run_fork_reply_fallback_path_no_forcing() -> None:
    invoker = FakeInvoker(_response(text="直接回答"))
    context = build_fork_context(system_prompt="s", messages=(), tools=(_other_definition(),))
    from pygent import UserMessage

    outcome = await run_fork_reply(
        invoker=invoker,
        model_group=object(),
        retry_policy=object(),
        context=context,
        user_message=UserMessage(content="hi"),
        force_speak=False,
    )
    assert outcome.via == "text"
    assert outcome.text == "直接回答"
    call = invoker.calls[0]
    assert isinstance(call["generation"], GenerationConfig)
    assert call["generation"].tool_choice is None


async def test_run_fork_reply_passes_deadline() -> None:
    invoker = FakeInvoker(_response(text="x"))
    context = build_fork_context(system_prompt="s", messages=(), tools=())
    from pygent import UserMessage

    await run_fork_reply(
        invoker=invoker,
        model_group=object(),
        retry_policy=object(),
        context=context,
        user_message=UserMessage(content="hi"),
        deadline=1234.5,
    )
    assert invoker.calls[0]["deadline"] == 1234.5


def test_forced_generation_constant() -> None:
    assert FORCED_SPEAK_GENERATION.tool_choice == "speak"
