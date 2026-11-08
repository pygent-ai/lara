"""Feishu delivery channel for speak content (the D-RT10 channel slot).

Credentials come from the process environment (``FEISHU_APP_ID`` /
``FEISHU_APP_SECRET``, loaded from ``~/.lara/credentials.env``); the target
chats are the ``oc_*`` keys of ``~/.lara/feishu-bridge/sessions.json`` with
``"enabled": true``. Delivery is best effort: failures are logged and never
break the speak tool result or the turn.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

OPEN_BASE = "https://open.feishu.cn"
# v2 OAuth with the client_credentials grant returns the app access token
# (v1 /authen/v1/tenant_access_token/internal is not served on this edge).
TOKEN_URL = f"{OPEN_BASE}/open-apis/authen/v2/oauth/token"
MESSAGE_URL = f"{OPEN_BASE}/open-apis/im/v1/messages"
REQUEST_TIMEOUT_SECONDS = 10.0
# 失效/缺失 token 的错误码（HTTP 200 业务码或 HTTP 4xx JSON body 均可能出现）：
# 刷新一次 token 后重试
TOKEN_ERROR_CODES = {99991661, 99991663, 99991668, 99991679}
TOKEN_EXPIRY_BUFFER_SECONDS = 60.0

_logger = logging.getLogger(__name__)


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    token: str | None = None,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        # Feishu reports auth problems as HTTP 4xx with a JSON code body;
        # surface that body so the code-based refresh logic can react.
        try:
            return json.loads(error.read().decode("utf-8"))
        except ValueError:
            raise


class FeishuChannel:
    """Delivers speak text to one Feishu chat via a tenant-access token."""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        chat_id: str,
        post: Callable[..., dict[str, Any]] = _post_json,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.chat_id = chat_id
        self._post = post
        self._monotonic = monotonic
        self._token_lock = threading.Lock()
        self._token_value: str | None = None
        self._token_expires_at = 0.0

    def deliver(self, text: str) -> bool:
        """Send one text message; never raises — the channel must not break turns."""

        try:
            return self._deliver(text)
        except Exception:  # noqa: BLE001 - best-effort delivery
            _logger.warning("feishu channel delivery failed", exc_info=True)
            return False

    def _deliver(self, text: str) -> bool:
        payload = {
            "receive_id": self.chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        url = f"{MESSAGE_URL}?receive_id_type=chat_id"
        response = self._post(url, payload, token=self._token())
        if response.get("code") in TOKEN_ERROR_CODES:
            response = self._post(url, payload, token=self._token(force=True))
        if response.get("code") != 0:
            raise RuntimeError(
                f"feishu message error code={response.get('code')}: {response.get('msg')}"
            )
        return True

    def _token(self, *, force: bool = False) -> str:
        with self._token_lock:
            if (
                not force
                and self._token_value
                and self._monotonic() < self._token_expires_at - TOKEN_EXPIRY_BUFFER_SECONDS
            ):
                return self._token_value
            response = self._post(
                TOKEN_URL,
                {
                    "grant_type": "client_credentials",
                    "client_id": self.app_id,
                    "client_secret": self.app_secret,
                },
            )
            if response.get("code") != 0:
                raise RuntimeError(
                    f"feishu token error code={response.get('code')}: {response.get('msg')}"
                )
            self._token_value = str(response["access_token"])
            self._token_expires_at = self._monotonic() + float(
                response.get("expires_in", 7200)
            )
            return self._token_value


@dataclass(frozen=True)
class FeishuBridgeConfig:
    """Credentials and target chats shared by the outbound channel and the
    inbound bridge; all of it comes from the user's bridge config file plus
    the environment."""

    app_id: str
    app_secret: str
    chats: tuple[str, ...]


def load_feishu_bridge_config(user_root: str | Path) -> FeishuBridgeConfig | None:
    """Build the configured Feishu bridge config; None when anything is missing.

    Activation requires all three: the app credentials in the environment,
    ``~/.lara/feishu-bridge/sessions.json`` present, and ``"enabled": true`` in
    it. The targets are the file's ``oc_*`` chat keys.
    """

    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        return None
    sessions_file = Path(user_root).expanduser() / "feishu-bridge" / "sessions.json"
    if not sessions_file.is_file():
        return None
    try:
        data = json.loads(sessions_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _logger.warning("feishu bridge sessions.json is unreadable", exc_info=True)
        return None
    if data.get("enabled") is not True:
        return None
    chats = tuple(
        str(chat)
        for chat in (data.get("sessions") or {})
        if str(chat).startswith("oc_")
    )
    return FeishuBridgeConfig(app_id=app_id, app_secret=app_secret, chats=chats)


def load_feishu_channels(user_root: str | Path) -> tuple[FeishuChannel, ...]:
    config = load_feishu_bridge_config(user_root)
    if config is None:
        return ()
    return tuple(
        FeishuChannel(app_id=config.app_id, app_secret=config.app_secret, chat_id=chat)
        for chat in config.chats
    )


__all__ = [
    "FeishuBridgeConfig",
    "FeishuChannel",
    "load_feishu_bridge_config",
    "load_feishu_channels",
]
