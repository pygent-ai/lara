from __future__ import annotations

import logging
from time import perf_counter
from typing import Any


_LOGGER = logging.getLogger("uvicorn.error")
_TIMED_PREFIXES = ("/sessions", "/traces")
_SLOW_REQUEST_MS = 100.0
_LARGE_RESPONSE_BYTES = 128 * 1024


class RequestTimingMiddleware:
    """Log end-to-end timings for the session-switch read path.

    This is a small ASGI wrapper rather than ``BaseHTTPMiddleware`` so response
    streaming semantics stay untouched.  The measurement finishes after the
    final response body chunk and therefore includes response serialization.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = str(scope.get("path") or "")
        if scope.get("type") != "http" or not path.startswith(_TIMED_PREFIXES):
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        status = 500
        response_bytes = 0
        finished = False

        async def timed_send(message: dict[str, Any]) -> None:
            nonlocal status, response_bytes, finished
            if message["type"] == "http.response.start":
                status = int(message.get("status") or status)
            elif message["type"] == "http.response.body":
                response_bytes += len(message.get("body") or b"")
                finished = not message.get("more_body", False)
            await send(message)

        try:
            await self.app(scope, receive, timed_send)
        finally:
            duration_ms = (perf_counter() - started) * 1000
            query = bytes(scope.get("query_string") or b"").decode(
                "latin-1", errors="replace"
            )
            target = f"{path}?{query}" if query else path
            log = (
                _LOGGER.info
                if duration_ms >= _SLOW_REQUEST_MS
                or response_bytes >= _LARGE_RESPONSE_BYTES
                or status >= 400
                else _LOGGER.debug
            )
            log(
                "[performance] request method=%s target=%s status=%d "
                "duration_ms=%.1f response_bytes=%d complete=%s",
                scope.get("method") or "",
                target,
                status,
                duration_ms,
                response_bytes,
                finished,
            )
