"""Conversation-exchange envelope injected after the next tool result (D-RT1).

When the fork child answers an interrupt, the main agent must learn what
happened without being interrupted itself: the exchange rides along as a
system reminder attached to the next tool result. See
docs/design/realtime-interaction 3.3.2/3.3.3 for the timing diagrams.
"""

from __future__ import annotations

from lara.agents.context import system_reminder


def render_exchange_envelope(*, user_asked: str, response: str) -> str:
    """Build the ``<system-reminder>`` envelope for one fork-reply exchange."""

    inner = "\n".join(
        (
            "<user-asked>",
            user_asked.strip(),
            "</user-asked>",
            "<your-response-in-another-session>",
            response.strip(),
            "</your-response-in-another-session>",
        )
    )
    return system_reminder(inner)


__all__ = ["render_exchange_envelope"]
