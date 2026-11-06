"""N-rounds-without-speak reminder (the "agent volunteers information" path).

While working, the agent should not go silent for too long: after N tool
rounds without a speak call, the harness attaches this reminder after the
tool result (same injection point as the exchange envelope) so the agent
decides whether there is something worth saying. The round counting is owned
by the runtime wiring; this module owns the threshold rule and the text.
"""

from __future__ import annotations

DEFAULT_NUDGE_INTERVAL = 5

NUDGE_REMINDER = (
    "You have not spoken to the user for {rounds} tool rounds. If there is "
    "something worth telling them — progress, a decision, a blocker — say it "
    "via speak now. If there is genuinely nothing to say, keep working "
    "silently."
)


def should_nudge(
    *, rounds_since_speak: int, interval: int = DEFAULT_NUDGE_INTERVAL
) -> bool:
    """True when the agent has been silent for at least ``interval`` rounds."""

    if interval <= 0:
        raise ValueError("interval must be positive")
    return rounds_since_speak >= interval


def nudge_reminder(*, rounds_since_speak: int) -> str:
    """Build the reminder text with the actual silent-round count."""

    return NUDGE_REMINDER.format(rounds=rounds_since_speak)


__all__ = [
    "DEFAULT_NUDGE_INTERVAL",
    "NUDGE_REMINDER",
    "nudge_reminder",
    "should_nudge",
]
