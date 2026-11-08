"""Inbound Feishu bridge: relays bound-chat messages into the dedicated
``Lara说`` session.

Runs the Feishu long-connection (WebSocket) client on a daemon thread —
no public address is needed. A user text message from a bound chat becomes:

- a fresh turn when the session is idle (its speak replies flow back to
  Feishu through the outbound channel), or
- a fork reply when a turn is already running (the reply text is sent back
  to Feishu here, because fork replies never emit speak events).

The bot's own messages are ignored, so outbound deliveries never loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from collections import deque

from lara.core.io import read_json
from lara.runtime.realtime.feishu import FeishuBridgeConfig, load_feishu_bridge_config
from lara.sessions import LARA_SAYS_SESSION_MODE
from lara_api.models.requests import ChatTurnRequest
from lara_api.project_state import LARA_SAYS_SCOPE_ID
from lara_api.services.session_service import session_service_for_scope

_logger = logging.getLogger(__name__)


def _sender_type(event) -> str:
    sender = getattr(event, "sender", None)
    return str(getattr(sender, "sender_type", "") or "")


def _message_field(event, name: str) -> str:
    message = getattr(event, "message", None)
    return str(getattr(message, name, "") or "")


def _message_text(event) -> str:
    message = getattr(event, "message", None)
    raw = str(getattr(message, "content", "") or "")
    try:
        return str(json.loads(raw).get("text", "") or "").strip()
    except ValueError:
        return ""


class FeishuBridge:
    """Owns the long-connection client and the relay into the dedicated session."""

    def __init__(self, context, *, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._context = context
        self._loop = loop
        self._config: FeishuBridgeConfig | None = None
        self._seen_message_ids: deque[str] = deque(maxlen=256)
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """Start the long-connection client; returns False when not configured.

        Must be called from the API process's event loop (the bridge marshals
        relay work onto that loop).
        """

        self._config = load_feishu_bridge_config(self._context.config.user_lara_root)
        if self._config is None:
            _logger.info("feishu bridge not configured; inbound relay stays off")
            return False
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        self._thread = threading.Thread(
            target=self._run_client, name="feishu-bridge", daemon=True
        )
        self._thread.start()
        _logger.info(
            "feishu bridge started for chats %s", list(self._config.chats)
        )
        return True

    def _run_client(self) -> None:
        config = self._config
        if config is None:
            return
        try:
            import lark_oapi as lark
        except ImportError:  # pragma: no cover - the dependency ships with the app
            _logger.warning("lark-oapi is not installed; feishu bridge cannot start")
            return
        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_event)
            .build()
        )
        client = lark.ws.Client(
            config.app_id,
            config.app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.INFO,
        )
        # Blocks the daemon thread; the SDK owns reconnects (auto_reconnect).
        client.start()

    def _on_event(self, data) -> None:
        try:
            event = getattr(data, "event", None)
            message_type = _message_field(event, "message_type")
            chat_id = _message_field(event, "chat_id")
            message_id = _message_field(event, "message_id")
            sender_type = _sender_type(event)
            text = _message_text(event)
            if message_type != "text" or not text:
                return
            if self._config is None or chat_id not in self._config.chats:
                return
            if sender_type == "app":
                return  # the bot's own outbound deliveries must never loop
            if message_id in self._seen_message_ids:
                return  # Feishu redelivers events; relay each message once
            self._seen_message_ids.append(message_id)
            self._schedule_relay(text)
        except Exception:  # noqa: BLE001 - the SDK callback must never crash
            _logger.warning("feishu bridge event handling failed", exc_info=True)

    def _schedule_relay(self, text: str) -> None:
        assert self._loop is not None  # start() captures the API process loop
        future = asyncio.run_coroutine_threadsafe(self._relay(text), self._loop)
        future.add_done_callback(self._log_relay_result)

    @staticmethod
    def _log_relay_result(future) -> None:
        error = future.exception()
        if error is not None:
            _logger.warning("feishu bridge relay failed", exc_info=error)

    async def _relay(self, text: str) -> None:
        service = session_service_for_scope(self._context, LARA_SAYS_SCOPE_ID)
        record = next(
            (
                item
                for item in service.list_chat_sessions()
                if item.mode == LARA_SAYS_SCOPE_ID
            ),
            None,
        )
        if record is None:
            record = service.create_session(mode=LARA_SAYS_SESSION_MODE)
        session_id = record.session_id

        execution_id = self._active_execution_id(service, session_id)
        if execution_id is not None:
            try:
                receipt = await self._context.chat_registry.deliver_steering(
                    execution_id,
                    session_id=session_id,
                    input_id=f"feishu-{uuid.uuid4().hex[:12]}",
                    message=text,
                )
            except LookupError:
                execution_id = None  # the run is not actually live anymore
            else:
                reply = receipt.reply
                reply_text = str(reply.get("text") or "").strip()
                if reply_text and not reply.get("say_nothing"):
                    await self._send_to_feishu(reply_text)
                return
        if execution_id is None:
            request = ChatTurnRequest(
                session_id=session_id, scope_id=LARA_SAYS_SCOPE_ID, message=text
            )
            run = await self._context.chat_registry.resolve(self._context, request)
            # The turn runs on the session lane; observing its stream keeps the
            # bridge's stake in it alive until completion.
            async for _ in run.events(after=0):
                pass

    async def _send_to_feishu(self, text: str) -> None:
        from lara.runtime.realtime.feishu import FeishuChannel

        if self._config is None:
            return
        for chat in self._config.chats:
            channel = FeishuChannel(
                app_id=self._config.app_id,
                app_secret=self._config.app_secret,
                chat_id=chat,
            )
            await asyncio.to_thread(channel.deliver, text)

    def _active_execution_id(self, service, session_id: str) -> str | None:
        record = next(
            (
                item
                for item in service.list_chat_sessions()
                if item.session_id == session_id
            ),
            None,
        )
        if (
            record is None
            or record.last_case_run_status != "running"
            or not record.last_case_run_id
        ):
            return None
        run_metadata = read_json(
            service.manager.sessions_root
            / session_id
            / "cases"
            / record.case_id
            / "runs"
            / record.last_case_run_id
            / "run_metadata.json",
            default={},
        )
        return str(run_metadata.get("runtime_execution_id") or "") or None
