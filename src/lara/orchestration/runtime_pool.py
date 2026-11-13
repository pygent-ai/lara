"""lara-facing runtime pool over the SDK workspace pool.

The SDK pool retires a scope's runtime only when a newer generation of the
same scope is acquired. ``ApiContext.areload`` switches workspaces eagerly, so
the old scope must be retired without waiting for a next acquire.
"""

from __future__ import annotations

from typing import Any

from pygent.agent.pygent_v1.orchestration.runtime_keys import RuntimeScopeKey
from pygent.agent.pygent_v1.orchestration.runtime_pool import (
    WorkspaceRuntimePool as _SdkWorkspaceRuntimePool,
)


class WorkspaceRuntimePool(_SdkWorkspaceRuntimePool):
    """SDK pool plus eager scope retirement."""

    async def retire_scope(self, scope: RuntimeScopeKey) -> None:
        runtime_to_close: Any | None = None
        async with self._lock:
            entry_id = self._active.pop(scope, None)
            entry = None if entry_id is None else self._entries.get(entry_id)
            if entry is not None:
                entry.retiring = True
                if entry.references == 0:
                    del self._entries[entry.entry_id]
                    runtime_to_close = entry.runtime.runtime_service
        if runtime_to_close is not None:
            await runtime_to_close.close(cancel=False)


__all__ = ["WorkspaceRuntimePool"]
