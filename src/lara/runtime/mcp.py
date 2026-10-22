"""Warm MCP stdio transport that keeps one server session across operations.

Pygent's ``MCPStdioTransport`` re-spawns the server subprocess for every
operation. That breaks servers which reach readiness asynchronously after
``initialize`` — e.g. ``open-browser-control`` connects to its bridge only
after startup, so a call issued immediately after initialize reliably loses
that race ("Browser extension not connected"). Holding one session open also
keeps bridge-style servers alive so their side channels stay connected.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import ClientSession

from pygent.tool.mcp import MCPStdioTransport


class _ReadinessRetryingSession:
    """Delegate to the shared session, retrying pre-action readiness rejections.

    The server rejects a call before dispatching any action while its own
    bridge connection is still starting up, so retrying the same call on the
    warm session converges without re-execution risk. Post-action timeouts
    are never retried here.
    """

    _RETRYABLE_MARKERS = (
        "Browser extension not connected",
        "Timeout waiting for session to start",
    )
    _RETRY_ATTEMPTS = 3
    _RETRY_DELAY_S = 1.5

    def __init__(self, session: Any) -> None:
        self._session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    async def call_tool(self, name: str, arguments: Any = None, **kwargs: Any) -> Any:
        result = await self._session.call_tool(name, arguments=arguments, **kwargs)
        for attempt in range(self._RETRY_ATTEMPTS - 1):
            if not result.is_error:
                break
            text = "\n".join(
                item.text for item in result.content if getattr(item, "text", None)
            )
            if not any(marker in text for marker in self._RETRYABLE_MARKERS):
                break
            await asyncio.sleep(self._RETRY_DELAY_S * (attempt + 1))
            result = await self._session.call_tool(
                name, arguments=arguments, **kwargs
            )
        return result


class WarmMCPStdioTransport:
    """Share one long-lived stdio session across all operations.

    The session context managers from the ``mcp`` SDK must be entered and
    exited inside the same task (anyio cancel scopes), while callers arrive
    from unrelated tasks — so a dedicated owner task enters the session and
    holds it until :meth:`aclose`. Callers only borrow the session object.
    If the server process dies, the next :meth:`session` restarts it.
    """

    def __init__(
        self,
        command: str,
        *,
        args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
        cwd: str | Path | None = None,
    ) -> None:
        self._cold = MCPStdioTransport(command, args=args, env=env, cwd=cwd)
        self._lifecycle_lock = asyncio.Lock()
        self._owner_task: asyncio.Task[None] | None = None
        self._session_ready: asyncio.Future[Any] | None = None
        self._shutdown = asyncio.Event()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[Any]:
        session = await self._ensure()
        yield _ReadinessRetryingSession(session)

    async def _ensure(self) -> ClientSession:
        async with self._lifecycle_lock:
            if self._owner_task is not None and self._owner_task.done():
                self._owner_task = None
            if self._owner_task is None:
                self._shutdown = asyncio.Event()
                self._session_ready = asyncio.get_running_loop().create_future()
                self._owner_task = asyncio.create_task(
                    self._owner(), name="lara-mcp-session-owner"
                )
            assert self._session_ready is not None
            return await self._session_ready

    async def _owner(self) -> None:
        try:
            async with self._cold.session() as session:
                ready = self._session_ready
                assert ready is not None
                if not ready.done():
                    ready.set_result(session)
                await self._shutdown.wait()
        except BaseException as exc:
            ready = self._session_ready
            if ready is not None and not ready.done():
                ready.set_exception(exc)
            # Owner exits; the next _ensure() restarts a fresh session.

    async def aclose(self) -> None:
        async with self._lifecycle_lock:
            if self._owner_task is None:
                return
            self._shutdown.set()
            try:
                await asyncio.wait_for(asyncio.shield(self._owner_task), 10)
            except (TimeoutError, asyncio.TimeoutError):
                self._owner_task.cancel()
            except Exception:
                pass
            self._owner_task = None


__all__ = ["WarmMCPStdioTransport"]
