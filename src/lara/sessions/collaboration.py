from __future__ import annotations

from typing import Any, Protocol

from pygent.agent.pygent_v1.sessions.collaboration import (
    AgentMessage,
    AgentMessageState,
    SessionCollaborationStore,
)


def agent_message_to_dict(
    self: AgentMessage, *, include_content: bool = False
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "message_id": self.message_id,
        "submission_id": self.submission_id,
        "source_session_id": self.source_session_id,
        "source_agent_alias": self.source_agent_alias,
        "target_session_id": self.target_session_id,
        "status": self.state.value,
        "created_at": self.created_at,
        "updated_at": self.updated_at,
        "delivered_at": self.delivered_at,
        "delivered_execution_id": self.delivered_execution_id,
    }
    if include_content:
        value["content"] = self.content
    return value


# SDK gap (see docs/design/pygent-sdk-migration): graph/pipeline.py renders
# the inbox through ``AgentMessage.to_dict`` but the SDK class does not define
# it. Patch the SDK type in place so every store and the pipeline share it;
# drop this once pygent ships the method.
if not hasattr(AgentMessage, "to_dict"):
    AgentMessage.to_dict = agent_message_to_dict  # type: ignore[attr-defined]


class _DictItem(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


def collaboration_item_dict(item: AgentMessage | _DictItem) -> dict[str, Any]:
    """JSON view of a collaboration item (agent message or dispatch view)."""

    if isinstance(item, AgentMessage):
        return agent_message_to_dict(item)
    return item.to_dict()


__all__ = [
    "AgentMessage",
    "AgentMessageState",
    "SessionCollaborationStore",
    "agent_message_to_dict",
    "collaboration_item_dict",
]
