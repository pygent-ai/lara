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
                "credential": {"env": "LARA_CONNECTION_SWITCH_KEY"},
                "protocols": {
                    "openai_chat_completions": {"base_url": base_url}
                },
                "verify_ssl": True,
            }
        },
        "models": {
            "primary": {
                "connection": "test-connection",
                "model_id": "test-model",
                "protocol": "openai_chat_completions",
                "provider_options": {},
                "capabilities": _capabilities(),
            }
        },
        "model_groups": {"coding": {"models": ["primary"]}},
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
                        "authorization": self.headers.get("Authorization"),
                        "body": json.loads(raw or b"{}"),
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


@pytest.mark.asyncio
async def test_existing_session_switches_connection_after_settings_change(tmp_path, monkeypatch):
    """Turn 1 hits connection A; after PATCH /settings rewrites the connection to
    provider B, the next turn of the SAME session must reach provider B."""
    monkeypatch.setenv("LARA_CONNECTION_SWITCH_KEY", "local-test-only")
    provider_a = _ProviderServer("AAA")
    provider_b = _ProviderServer("BBB")
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    user_root = home / ".lara"
    user_root.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    (user_root / "config.yaml").write_text(
        _config_yaml(provider_a.base_url), encoding="utf-8"
    )
    try:
        app = create_app(workspace_root=str(workspace))
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app), base_url="http://lara") as client:
                created = await client.post("/sessions", json={})
                assert created.status_code == 200, created.text
                session_id = created.json()["session_id"]

                first = await client.post(
                    "/chat/stream",
                    json={"session_id": session_id, "message": "turn one"},
                )
                assert first.status_code == 200, first.text
                assert provider_a.requests and not provider_b.requests

                patched = await client.patch(
                    "/settings",
                    json={
                        "model_config": _native_mapping(provider_b.base_url),
                        "default_model_group": "coding",
                    },
                )
                assert patched.status_code == 200, patched.text

                second = await client.post(
                    "/chat/stream",
                    json={"session_id": session_id, "message": "turn two"},
                )
                assert second.status_code == 200, second.text

                fresh = await client.post("/sessions", json={})
                fresh_id = fresh.json()["session_id"]
                third = await client.post(
                    "/chat/stream",
                    json={"session_id": fresh_id, "message": "turn three"},
                )
                assert third.status_code == 200, third.text

        assert provider_b.requests, "new connection never received a request"
        a_total, b_total = len(provider_a.requests), len(provider_b.requests)
        print(f"provider A requests: {a_total}, provider B requests: {b_total}")
        texts = _terminal_texts(second.text)
        print(f"turn-two terminal events: {texts}")
        # The second turn of the existing session must use the new connection.
        assert b_total >= 1
        assert "BBB" in "".join(texts), texts
    finally:
        provider_a.stop()
        provider_b.stop()


@pytest.mark.asyncio
async def test_existing_session_switches_credential_after_settings_change(tmp_path, monkeypatch):
    """Changing only the credential value (same base_url) must also rotate the
    runtime, so the next turn of an existing session sends the new API key."""
    monkeypatch.setenv("LARA_CONNECTION_SWITCH_KEY", "local-test-only")
    provider = _ProviderServer("AAA")
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
                session_id = created.json()["session_id"]
                first = await client.post(
                    "/chat/stream",
                    json={"session_id": session_id, "message": "turn one"},
                )
                assert first.status_code == 200, first.text
                assert provider.requests[0]["authorization"] == "Bearer local-test-only"

                patched = await client.patch(
                    "/settings",
                    json={
                        "model_config": _native_mapping(provider.base_url),
                        "default_model_group": "coding",
                        "credential_values": {"LARA_CONNECTION_SWITCH_KEY": "rotated-key"},
                    },
                )
                assert patched.status_code == 200, patched.text

                second = await client.post(
                    "/chat/stream",
                    json={"session_id": session_id, "message": "turn two"},
                )
                assert second.status_code == 200, second.text
                print(
                    "credential headers:",
                    [request["authorization"] for request in provider.requests],
                )
                assert provider.requests[1]["authorization"] == "Bearer rotated-key"
    finally:
        provider.stop()


@pytest.mark.asyncio
async def test_conversation_scope_session_after_settings_change(tmp_path, monkeypatch):
    """The desktop '独立对话' scope builds its SessionManager from
    config_for_scope(); check whether it uses the model config at all."""
    monkeypatch.setenv("LARA_CONNECTION_SWITCH_KEY", "local-test-only")
    provider_a = _ProviderServer("AAA")
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    user_root = home / ".lara"
    user_root.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    (user_root / "config.yaml").write_text(
        _config_yaml(provider_a.base_url), encoding="utf-8"
    )
    try:
        app = create_app(workspace_root=str(workspace))
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app), base_url="http://lara") as client:
                turn = await client.post(
                    "/chat/stream",
                    json={"scope_id": "conversation", "message": "hello"},
                )
                print(f"conversation-scope turn status: {turn.status_code}")
                print(f"conversation-scope turn tail: {turn.text[-400:]}")
                assert turn.status_code == 200, turn.text
    finally:
        provider_a.stop()


@pytest.mark.asyncio
async def test_second_project_scope_session_after_settings_change(tmp_path, monkeypatch):
    """A session that belongs to a non-active project scope resolves its
    config through config_for_scope() -> load_run_config()."""
    monkeypatch.setenv("LARA_CONNECTION_SWITCH_KEY", "local-test-only")
    provider_a = _ProviderServer("AAA")
    provider_b = _ProviderServer("BBB")
    home = tmp_path / "home"
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    user_root = home / ".lara"
    user_root.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    (user_root / "config.yaml").write_text(
        _config_yaml(provider_a.base_url), encoding="utf-8"
    )
    try:
        app = create_app(workspace_root=str(workspace_a))
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app), base_url="http://lara") as client:
                # Remember workspace-b as a recent project so its scope appears.
                patched = await client.patch(
                    "/settings",
                    json={"workspace_root": str(workspace_b)},
                )
                assert patched.status_code == 200, patched.text
                patched = await client.patch(
                    "/settings",
                    json={"workspace_root": str(workspace_a)},
                )
                assert patched.status_code == 200, patched.text

                created = await client.post(
                    "/sessions",
                    json={"scope_id": f"project:{workspace_b.resolve()}"},
                )
                print(f"second-project create status: {created.status_code} {created.text[:200]}")
                assert created.status_code == 200, created.text
                session_id = created.json()["session_id"]

                first = await client.post(
                    "/chat/stream",
                    json={
                        "session_id": session_id,
                        "scope_id": f"project:{workspace_b.resolve()}",
                        "message": "turn one",
                    },
                )
                print(f"second-project turn status: {first.status_code}")
                print(f"second-project turn tail: {first.text[-300:]}")
                assert first.status_code == 200, first.text
                assert provider_a.requests, "first turn never reached provider A"

                # Change the connection while workspace-a is the active project.
                patched = await client.patch(
                    "/settings",
                    json={
                        "model_config": _native_mapping(provider_b.base_url),
                        "default_model_group": "coding",
                    },
                )
                assert patched.status_code == 200, patched.text

                second = await client.post(
                    "/chat/stream",
                    json={
                        "session_id": session_id,
                        "scope_id": f"project:{workspace_b.resolve()}",
                        "message": "turn two",
                    },
                )
                assert second.status_code == 200, second.text
                texts = _terminal_texts(second.text)
                print(f"second-project turn-two deltas: {texts}")
                assert provider_b.requests, "existing second-project session kept the old connection"
                assert "BBB" in "".join(texts), texts
    finally:
        provider_a.stop()
        provider_b.stop()


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
