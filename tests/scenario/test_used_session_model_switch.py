from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from lara_api.app import create_app


def _capabilities() -> dict[str, Any]:
    return {
        "modalities": {"input": ["text"], "output": ["text"]},
        "streaming": {"output": ["text"]},
        "tools": {"call": True, "choice": ["auto"], "parallel": False},
        "structured_output": {"json_object": True, "json_schema": False},
        "reasoning": {"supported": False, "controllable": False},
        "limits": {"context_tokens": 1000, "max_output_tokens": 100},
    }


def _native_mapping(base_url: str) -> dict[str, Any]:
    return {
        "connections": {
            "test-connection": {
                "provider": "test",
                "credential": {"env": "LARA_MODEL_SWITCH_KEY"},
                "protocols": {
                    "openai_chat_completions": {"base_url": base_url}
                },
                "verify_ssl": True,
            }
        },
        "models": {
            "primary": {
                "connection": "test-connection",
                "model_id": "model-primary",
                "protocol": "openai_chat_completions",
                "provider_options": {},
                "capabilities": _capabilities(),
            },
            "backup": {
                "connection": "test-connection",
                "model_id": "model-backup",
                "protocol": "openai_chat_completions",
                "provider_options": {},
                "capabilities": _capabilities(),
            },
        },
        "model_groups": {"coding": {"models": ["primary", "backup"]}},
    }


def _sse_body(marker: str) -> bytes:
    chunks = [
        {"choices": [{"delta": {"content": marker}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}},
    ]
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    return body.encode()


class _ProviderServer:
    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.requests: list[dict[str, Any]] = []
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                provider.requests.append(
                    {
                        "path": self.path,
                        "model": (json.loads(raw or b"{}") or {}).get("model"),
                    }
                )
                body = _sse_body(provider.marker)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _terminal_texts(sse_text: str) -> list[str]:
    texts = []
    for line in sse_text.splitlines():
        if not line.startswith("data: "):
            continue
        event = json.loads(line[6:])
        if event["kind"] == "model.text.delta":
            text = event["data"].get("text")
            if isinstance(text, str):
                texts.append(text)
        if event["kind"] in {"execution.completed", "execution.failed"}:
            texts.append(event["kind"])
    return texts


@pytest.mark.asyncio
async def test_used_session_switches_model_within_same_group(tmp_path, monkeypatch):
    """A used session must actually run on the newly selected model of its
    fixed group after PATCH /sessions/{id}/model."""
    monkeypatch.setenv("LARA_MODEL_SWITCH_KEY", "local-test-only")
    provider = _ProviderServer("OK")
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    user_root = home / ".lara"
    user_root.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    (user_root / "config.yaml").write_text(
        _config_yaml(provider.base_url), encoding="utf-8"
    )
    try:
        app = create_app(workspace_root=str(workspace))
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app), base_url="http://lara") as client:
                created = await client.post("/sessions", json={})
                assert created.status_code == 200, created.text
                session_id = created.json()["session_id"]
                assert created.json()["model_group_name"] == "coding"
                assert created.json()["selected_model_key"] == "primary"

                first = await client.post(
                    "/chat/stream",
                    json={"session_id": session_id, "message": "turn one"},
                )
                assert first.status_code == 200, first.text
                assert provider.requests[-1]["model"] == "model-primary", provider.requests

                switched = await client.patch(
                    f"/sessions/{session_id}/model",
                    json={"selected_model_key": "backup"},
                )
                assert switched.status_code == 200, switched.text
                assert switched.json()["selected_model_key"] == "backup"

                detail = await client.get(f"/sessions/{session_id}")
                assert detail.status_code == 200, detail.text
                body = detail.json()
                assert body["session"]["selected_model_key"] == "backup"
                assert [m["model_key"] for m in body["selectable_models"]] == [
                    "primary",
                    "backup",
                ]

                groups = await client.get("/sessions/groups")
                assert groups.status_code == 200, groups.text
                records = [
                    item
                    for group in groups.json()["groups"]
                    for item in group["sessions"]
                    if item["session_id"] == session_id
                ]
                assert records and records[0]["selected_model_key"] == "backup"

                second = await client.post(
                    "/chat/stream",
                    json={"session_id": session_id, "message": "turn two"},
                )
                assert second.status_code == 200, second.text
                assert provider.requests[-1]["model"] == "model-backup", provider.requests

                back = await client.patch(
                    f"/sessions/{session_id}/model",
                    json={"selected_model_key": "primary"},
                )
                assert back.status_code == 200, back.text
                third = await client.post(
                    "/chat/stream",
                    json={"session_id": session_id, "message": "turn three"},
                )
                assert third.status_code == 200, third.text
                assert provider.requests[-1]["model"] == "model-primary", provider.requests
    finally:
        provider.stop()


def _config_yaml(base_url: str) -> str:
    from lara.config.yaml_subset import dump_yaml_subset

    return dump_yaml_subset(
        {
            **_native_mapping(base_url),
            "agent": {"default_alias": "default"},
            "agents": [
                {
                    "alias": "default",
                    "model_request": {"default_model_group": "coding"},
                }
            ],
            "runtime": {"approvals": {"enabled": False}},
        }
    )
