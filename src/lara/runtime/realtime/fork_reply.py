"""Fork-snapshot single-call reply (D-RT2/D-RT3).

The interrupt path: copy the main agent's request state verbatim (cache
prefix), append the user's message, and make ONE model call. Delivery is
capability-gated (D-RT3):

- main path:    ``GenerationConfig(tool_choice="speak")`` forces the reply
    through the speak tool; the speak arguments are extracted and returned.
- fallback path: no forcing — the model replies in plain text and the harness
    renders the final answer (``via="text"``).

The call reuses the runtime's existing ``LaraModelInvoker`` so credentials,
admission and reasoning-event relay behave exactly like foreground calls. The
execution is transient by design: lost on crash, the user simply re-asks.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pygent import (
    AIMessage,
    Context,
    GenerationConfig,
    Message,
    RetryPolicy,
    ToolDefinition,
)

from lara.runtime.realtime.speak import SpeakArgs, parse_speak_args

SPEAK_TOOL_NAME = "speak"

FORCED_SPEAK_GENERATION = GenerationConfig(tool_choice=SPEAK_TOOL_NAME)
FALLBACK_GENERATION = GenerationConfig()


@dataclass(frozen=True)
class ForkReplyOutcome:
    """One fork reply; ``via`` is ``"speak"`` (main path) or ``"text"`` (fallback)."""

    via: str
    speak_args: SpeakArgs | None
    text: str | None
    message: AIMessage


def build_fork_context(
    *,
    system_prompt: str,
    messages: Sequence[Message],
    tools: Sequence[ToolDefinition],
) -> Context:
    """Copy the request state verbatim — this prefix IS the cache key."""

    return Context(
        system_prompt=system_prompt,
        messages=tuple(messages),
        tools=tuple(tools),
    )


def extract_speak_args(message: AIMessage) -> SpeakArgs | None:
    """Find the speak call in a model response and validate its arguments.

    ``ToolCall.arguments`` is frozen at construction (``FrozenJsonObject`` —
    a Mapping, not a dict), so the raw value is normalized via ``dict``.
    """

    for call in message.tool_calls:
        if call.name != SPEAK_TOOL_NAME:
            continue
        return parse_speak_args(dict(call.arguments))
    return None


async def run_fork_reply(
    *,
    invoker,
    model_group,
    retry_policy: RetryPolicy,
    context: Context,
    user_message: Message,
    force_speak: bool = True,
    deadline: float | None = None,
) -> ForkReplyOutcome:
    """Run the single fork model call and normalize the reply.

    ``force_speak`` selects the delivery path (D-RT3); the caller gates it on
    the model's declared ``tools.choice`` capability. ``context`` must carry
    the copied prefix **including** the speak tool definition, and must NOT
    contain the user message — it is passed separately as ``user_message``
    (pygent appends ``current`` after ``context.messages``).
    """

    generation = FORCED_SPEAK_GENERATION if force_speak else FALLBACK_GENERATION
    execution = invoker.execute(
        model_group=model_group,
        retry_policy=retry_policy,
        generation=generation,
        message=user_message,
        context=context,
        tools=tuple(context.tools),
        deadline=deadline,
    )
    response = await execution.result()
    message = response.message

    if force_speak:
        speak_args = extract_speak_args(message)
        if speak_args is not None:
            return ForkReplyOutcome(
                via="speak", speak_args=speak_args, text=None, message=message
            )
        # Forced calls should always carry speak; a stray text response is
        # still deliverable through the fallback rendering.
    return ForkReplyOutcome(
        via="text", speak_args=None, text=message.content or "", message=message
    )


__all__ = [
    "FALLBACK_GENERATION",
    "FORCED_SPEAK_GENERATION",
    "ForkReplyOutcome",
    "SPEAK_TOOL_NAME",
    "build_fork_context",
    "extract_speak_args",
    "run_fork_reply",
]
