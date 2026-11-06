"""End-of-turn bottom-line speak check (D-RT11).

Fires when the model returns a response **without tool calls** — the moment a
turn would normally end. In speak-mode that final text is invisible to the
user, so before ending, the agent must make a conscious decision: deliver the
text through ``speak`` (rephrasing freely), or explicitly decline via
``speak(say_nothing=true)``.

The reminder points at the draft instead of quoting it: the draft is the
immediately preceding assistant message, already in context — quoting it would
duplicate tokens right next to the original.

Case "final response is a speak call" belongs to ``speak.execute_speak``
(``say_nothing=true`` → ``acknowledged (silent)``, nothing rendered) and never
reaches this module.
"""

from __future__ import annotations

from dataclasses import dataclass

TURN_FINALIZER_REMINDER = (
    "Your turn is about to end. The final text you just produced is NOT visible "
    "to the user — speak is the only channel they see. Decide now: call "
    "speak(text=…) to deliver it (rephrase freely), or call "
    "speak(say_nothing=true) to deliberately say nothing."
)


@dataclass(frozen=True)
class FinalizationDecision:
    """``action`` is ``"nudge"`` (inject reminder, continue the turn) or ``"end"``."""

    action: str
    reminder: str | None


def decide_turn_finalization(
    *, final_text: str, nudge_used: bool
) -> FinalizationDecision:
    """Decide whether a pure-text turn ending needs the bottom-line nudge.

    ``nudge_used`` is per-turn state owned by the runtime: the nudge fires at
    most once per turn (D-RT11); a second pure-text ending after the nudge is
    accepted as a deliberate silent finish, guarding against loops. An empty
    final text still nudges once — the model must decide explicitly.
    """

    if nudge_used:
        return FinalizationDecision(action="end", reminder=None)
    return FinalizationDecision(action="nudge", reminder=TURN_FINALIZER_REMINDER)


__all__ = ["FinalizationDecision", "decide_turn_finalization"]
