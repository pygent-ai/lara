"""Relay bound Feishu chat messages into the dedicated Lara Says session.

The bridge receives events over a long connection. The SDK speak outbox
delivers both foreground and fork replies through the configured channels.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import deque

from lara.runtime.realtime.feishu import FeishuBridgeConfig, load_feishu_bridge_config
from lara.sessions import LARA_SAYS_SESSION_MODE
from lara_api.project_state import LARA_SAYS_SCOPE_ID
from lara_api.services.chat_runner import ActiveChatRun
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

        self._config = load_feishu_bridge_config(self._context.config.user_pygent_root)
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
            if not message_id or message_id in self._seen_message_ids:
                return  # Feishu redelivers events; relay each message once
            self._seen_message_ids.append(message_id)
            self._schedule_relay(text, message_id)
        except Exception:  # noqa: BLE001 - the SDK callback must never crash
            _logger.warning("feishu bridge event handling failed", exc_info=True)

    def _schedule_relay(self, text: str, message_id: str) -> None:
        assert self._loop is not None  # start() captures the API process loop
        future = asyncio.run_coroutine_threadsafe(self._relay(text, message_id), self._loop)
        future.add_done_callback(self._log_relay_result)

    @staticmethod
    def _log_relay_result(future) -> None:
        error = future.exception()
        if error is not None:
            _logger.warning("feishu bridge relay failed", exc_info=error)

    async def _relay(self, text: str, message_id: str) -> None:
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

        delivery = await self._context.execution_host.deliver_user_message(
            config=service.manager.config,
            manager=service.manager,
            session_id=session_id,
            input_id=f"feishu-{message_id}",
            message=text,
        )
        if delivery.mode == "interrupt":
            return
        assert delivery.turn is not None
        await delivery.turn.wait_ready()
        async for _ in ActiveChatRun(delivery.turn).events(after=0):
            pass
