from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
from pygent.runtime.codec import context_from_dict

from pygent.lara.runtime.context import LARA_CONTEXT_CODECS, LaraContext
from lara.runtime.service import SessionCompressionUnavailable
from lara.sessions import SessionManager
from tests.runtime_recovery_support import recovery_service


def _seed_transcript(manager: SessionManager, session_id: str) -> None:
    session = manager.load(session_id)
    session.history = [
        {"role": "user", "content": f"prior question {'x' * 50_000}"},
        {
            "role": "assistant",
            "content": "prior answer",
            "tool_calls": [],
            "usage": {},
        },
    ]
    manager.save(session)


@pytest.mark.asyncio
async def test_compress_session_persists_the_snapshot_checkpoint() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        config, service = recovery_service(workspace, force_compression=True)
        manager = SessionManager(config)
        session_ref = manager.create(case_id="replay", mode="chat")
        _seed_transcript(manager, session_ref.session_id)

        try:
            result = await service.compress_session(
                manager=manager,
                session_id=session_ref.session_id,
                request_id="compress-test-1",
            )

            assert result["compression_count"] == 1
            assert result["message_count"] == 1

            session = manager.load(session_ref.session_id)
            agent_context = context_from_dict(
                session.metadata["agent_context"], registry=LARA_CONTEXT_CODECS
            )
            assert isinstance(agent_context, LaraContext)
            assert [message.role for message in agent_context.messages] == ["user"]
            assert "Durable recovery summary." in agent_context.messages[0].content
            assert agent_context.compression_count == 1
            assert agent_context.last_input_tokens == 0
            assert session.metadata["agent_context_history_count"] == len(
                session.history
            )
        finally:
            await service.close()


@pytest.mark.asyncio
async def test_compress_session_rejects_unsupported_configurations() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        config, service = recovery_service(workspace)
        manager = SessionManager(config)
        eternal_ref = manager.create(
            case_id="replay", mode="chat", eternal_memory=True
        )
        empty_ref = manager.create(case_id="replay", mode="chat")
        config.context_compression_enabled = False
        disabled_ref = manager.create(case_id="replay", mode="chat")

        try:
            for session_id in (
                eternal_ref.session_id,
                disabled_ref.session_id,
                empty_ref.session_id,
            ):
                with pytest.raises(SessionCompressionUnavailable):
                    await service.compress_session(
                        manager=manager,
                        session_id=session_id,
                        request_id=f"compress-{session_id}",
                    )
        finally:
            await service.close()
