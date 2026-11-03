from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from httpx import ASGITransport, AsyncClient

from lara_api.app import create_app
from tests.native_config_support import native_model_config_yaml


@pytest.mark.asyncio
async def test_api_resume_continues_a_failed_session(tmp_path, monkeypatch):
    """A model failure pauses the session; one empty resume send continues it.

    The first turn fails against a broken provider, the session reports
    resumable, and resuming submits a continuation turn that succeeds against
    the recovered provider without any new user input.
    """
    requests = []
    fail_next = {"value": True}

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            encoded = b'{"error": {"message": "provider down"}}'
            if fail_next["value"]:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return
            chunks = [
                {'choices': [{'delta': {'content': 'Continued.'}}]},
                {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
                {'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 3, 'total_tokens': 13}},
            ]
            sse = ''.join(f'data: {json.dumps(chunk)}\n\n' for chunk in chunks) + 'data: [DONE]\n\n'
            encoded = sse.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    home = tmp_path / 'home'
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    user_root = home / '.lara'
    user_root.mkdir(parents=True)
    monkeypatch.setattr(Path, 'home', lambda: home)
    monkeypatch.setenv('LARA_LOCAL_RESUME_TEST_KEY', 'local-test-only')
    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    (user_root / 'config.yaml').write_text(
        native_model_config_yaml(
            alias='test',
            model_id='local-test',
            base_url=f'http://127.0.0.1:{provider.server_port}/v1',
            credential_env='LARA_LOCAL_RESUME_TEST_KEY',
        ),
        encoding='utf-8',
    )

    def events_of(response):
        return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]

    try:
        app = create_app(workspace_root=str(workspace))
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app), base_url='http://lara') as client:
                created = await client.post('/sessions', json={})
                session_id = created.json()['session_id']

                # The first turn fails on the provider and pauses the session.
                failed = await client.post('/chat/stream', json={'session_id': session_id, 'message': 'Check readiness.'})
                terminal = next(event for event in events_of(failed) if event['kind'] == 'execution.failed')
                assert terminal['kind'] == 'execution.failed'
                detail = (await client.get(f'/sessions/{session_id}')).json()
                assert detail['session']['last_case_run_status'] == 'error'
                assert detail['session']['resumable'] is True

                # The provider recovers; one empty resume send continues the
                # session without any new user input.
                fail_next['value'] = False
                resumed = await client.post('/chat/stream', json={'session_id': session_id, 'resume': True})
                assert resumed.status_code == 200
                resumed_terminal = next(event for event in events_of(resumed) if event['kind'] == 'execution.completed')
                assert resumed_terminal['kind'] == 'execution.completed'

                detail = (await client.get(f'/sessions/{session_id}')).json()
                assert detail['session']['last_case_run_status'] == 'passed'
                assert detail['session']['resumable'] is False
                resume_rows = [
                    row for row in detail['history']
                    if row['role'] == 'user' and '<user-message>继续</user-message>' in row['content']
                ]
                assert len(resume_rows) == 1
                assert '<user-message>继续</user-message>' in resume_rows[0]['content']
                assert detail['history'][-1]['content'] == 'Continued.'
                # The resume turn reached the model with the continue signal.
                assert '<user-message>继续</user-message>' in requests[-1]['messages'][-1]['content']

                # A finished session has nothing left to resume.
                again = await client.post('/chat/stream', json={'session_id': session_id, 'resume': True})
                assert again.status_code == 200
                error_event = next(event for event in events_of(again) if event['kind'] == 'lara.transport.error')
                assert '无需恢复' in error_event['data']['error']
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
