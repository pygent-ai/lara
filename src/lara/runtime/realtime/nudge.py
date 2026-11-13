"""lora 产品层补充；基础实现全部来自 SDK ``pygent.agent.pygent_v1.runtime.realtime.nudge``。"""

from __future__ import annotations



DEFAULT_NUDGE_INTERVAL = 5

NUDGE_REMINDER = (
    "You have not spoken to the user for {rounds} tool rounds. If there is "
    "something worth telling them — progress, a decision, a blocker — say it "
    "via speak now. If there is genuinely nothing to say, keep working "
    "silently."
)

from pygent.agent.pygent_v1.runtime.realtime.nudge import (  # noqa: F401
    DEFAULT_NUDGE_INTERVAL,
    NUDGE_REMINDER,
    nudge_reminder,
)


def should_nudge(
    *, rounds_since_speak: int, interval: int = DEFAULT_NUDGE_INTERVAL
) -> bool:
    """True when the agent has been silent for at least ``interval`` rounds."""

    if interval <= 0:
        raise ValueError("interval must be positive")
    return rounds_since_speak >= interval


