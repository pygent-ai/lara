"""Tests for the exchange-envelope posting channel (D-RT1 wiring).

The envelope rides the existing agent-message queue: posting must produce a
queued AgentMessage that the runtime's reminder boundary can claim.
"""

from __future__ import annotations

import pytest

from pygent.lara.runtime.realtime.envelope import render_exchange_envelope
from pygent.lara.runtime.realtime.posting import queue_exchange_envelope
from lara.sessions import AgentMessageState, SessionCollaborationStore

SESSION_ID = "chat-realtime-posting-test"


def _store(tmp_path) -> SessionCollaborationStore:
    return SessionCollaborationStore(tmp_path / "lara-root")


def _post(store, *, submission_id="rtx-1", user_asked="进度如何", response="在做了"):
    return queue_exchange_envelope(
        store,
        session_id=SESSION_ID,
        submission_id=submission_id,
        user_asked=user_asked,
        response=response,
    )


def test_posted_envelope_is_queued_with_envelope_content(tmp_path) -> None:
    store = _store(tmp_path)
    message = _post(store)
    assert message.state is AgentMessageState.QUEUED
    assert message.target_session_id == SESSION_ID
    assert message.content == render_exchange_envelope(
        user_asked="进度如何", response="在做了"
    )
    assert "<user-asked>" in message.content
    assert "<your-response-in-another-session>" in message.content


def test_repost_same_submission_is_idempotent(tmp_path) -> None:
    store = _store(tmp_path)
    first = _post(store)
    second = _post(store)
    assert first.message_id == second.message_id


def test_same_submission_with_different_content_rejected(tmp_path) -> None:
    store = _store(tmp_path)
    _post(store)
    with pytest.raises(ValueError):
        _post(store, user_asked="换个问题")


def test_posted_envelope_is_claimable_by_the_reminder_boundary(tmp_path) -> None:
    store = _store(tmp_path)
    posted = _post(store)
    claimed = store.claim_messages(SESSION_ID, claim_id="claim-1", lease_seconds=30.0)
    assert len(claimed) == 1
    assert claimed[0].message_id == posted.message_id
    assert claimed[0].state is AgentMessageState.CLAIMED
    # A second boundary claim sees nothing new until the envelope is acked.
    assert store.claim_messages(SESSION_ID, claim_id="claim-2", lease_seconds=30.0) == []
