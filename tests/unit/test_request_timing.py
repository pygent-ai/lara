from __future__ import annotations

import logging

import pytest

from lara_api.services.request_timing import RequestTimingMiddleware


@pytest.mark.asyncio
async def test_session_request_timing_includes_serialized_response_bytes(caplog) -> None:
    body = b"x" * (128 * 1024)

    async def app(scope, receive, send) -> None:
        del scope, receive
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body})

    sent = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    caplog.set_level(logging.INFO, logger="uvicorn.error")
    middleware = RequestTimingMiddleware(app)
    await middleware(
        {
            "type": "http",
            "method": "GET",
            "path": "/sessions/example",
            "query_string": b"history_limit=200",
        },
        receive,
        send,
    )

    message = next(record.message for record in caplog.records if "[performance]" in record.message)
    assert "target=/sessions/example?history_limit=200" in message
    assert "status=200" in message
    assert "response_bytes=131072" in message
    assert "complete=True" in message
    assert sent[-1]["body"] == body
