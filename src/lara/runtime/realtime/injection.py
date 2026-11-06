"""Tool-result boundary nudge module (Phase 2 wiring).

The nudge rides the same injection seam as the reminders: after each tool
result, if the agent has been silent (no speak call) for ``interval``
rounds, the reminder text is appended to the tool result so the model
decides whether to speak. The module is instantiated per turn (one agent
execution per turn), so a plain counter is the whole state; a crash restarts
counting, which is acceptable for a soft UX reminder.
"""

from __future__ import annotations

from dataclasses import replace

from pygent import AIMessage, Module, ToolMessage

from lara.runtime.context import LaraContext

from .nudge import DEFAULT_NUDGE_INTERVAL, nudge_reminder
from .speak import SPEAK_TOOL_NAME


class _NudgeCounter:
    """Mutable per-turn round counter.

    Pygent freezes Module attributes after the first invoke, so the counter
    must live on a referenced object — rebinding ``self._counter`` would be
    rejected, mutating its field is not.
    """

    __slots__ = ("rounds",)

    def __init__(self) -> None:
        self.rounds = 0


class RealtimeNudgeModule(Module[ToolMessage, ToolMessage]):
    """Append the silent-rounds reminder at tool-result boundaries.

    The counter fires on exact multiples of ``interval`` (5, 10, ... rounds)
    so the reminder stays periodic instead of spamming every boundary. Any
    speak call in the round's assistant message resets the counter.

    The counter is a trusted live resource: deliberately ephemeral (lost on
    crash, which restarts counting) and never checkpointed.
    """

    trusted_live_resource_attributes = ("counter",)

    def __init__(self, interval: int = DEFAULT_NUDGE_INTERVAL) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        super().__init__()
        self.interval = interval
        self.counter = _NudgeCounter()

    async def forward(
        self, message: ToolMessage, context: LaraContext
    ) -> tuple[ToolMessage, LaraContext]:
        assistant = context.messages[-1] if context.messages else None
        spoke = isinstance(assistant, AIMessage) and any(
            call.name == SPEAK_TOOL_NAME for call in assistant.tool_calls
        )
        counter = self.counter
        counter.rounds = 0 if spoke else counter.rounds + 1
        if counter.rounds == 0 or counter.rounds % self.interval != 0:
            return message, context
        reminder = nudge_reminder(rounds_since_speak=counter.rounds)
        content = (
            f"{message.content}\n{reminder}" if message.content else reminder
        )
        return replace(message, content=content), context


__all__ = ["RealtimeNudgeModule"]
