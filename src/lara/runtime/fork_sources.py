"""Host fork-context sources.

Moved out of the kernel agents scaffold during the pygent.lara convergence:
these two adapters are lora host surface (the memory extractor's static
window and fork-product chaining) built on the kernel's ContextWindow.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from pygent.core import JsonValue
from pygent.lara.agents.context import ContextWindow, messages_from_history
from pygent.lara.agents.fork import Message


@dataclass(frozen=True)
class StaticContextSource:
    """A window built from an explicit system prompt and message list."""

    system_prompt: str
    messages: tuple[Message, ...] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    @classmethod
    def from_history(
        cls,
        *,
        system_prompt: str,
        history: Sequence[Mapping[str, JsonValue]],
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> "StaticContextSource":
        """Build a source from persisted conversation entries."""

        return cls(
            system_prompt=system_prompt,
            messages=messages_from_history(history),
            metadata=metadata or {},
        )

    def resolve(self) -> ContextWindow:
        return ContextWindow(
            system_prompt=self.system_prompt,
            messages=self.messages,
            metadata=self.metadata,
        )

    def describe(self) -> str:
        return f"static-messages:{len(self.messages)}"


@dataclass(frozen=True)
class ForkProductSource:
    """Reuse the resulting context of an earlier agent run as fork material.

    This is the chaining freedom of G3: a fork's product is itself a
    first-class context that later forks can derive from.
    """

    window: ContextWindow

    @classmethod
    def from_window_parts(
        cls,
        *,
        system_prompt: str,
        messages: tuple[Message, ...],
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> "ForkProductSource":
        return cls(window=ContextWindow(system_prompt, messages, metadata or {}))

    def resolve(self) -> ContextWindow:
        return self.window

    def describe(self) -> str:
        label = self.window.metadata.get("lara.agent.label", "fork-product")
        return f"fork-product:{label}"


__all__ = [
    "ForkProductSource",
    "StaticContextSource",
]
