"""Feishu delivery channel for speak content (the D-RT10 channel slot).

Speak text goes out as a text message; attachments are uploaded and sent as
image/file messages, because the SDK hands channels resolved paths rather than
pre-formatted text. Credentials come from the process environment
(``FEISHU_APP_ID`` / ``FEISHU_APP_SECRET``, loaded from
``~/.lara/credentials.env``); the target chats are the ``oc_*`` keys of
``~/.lara/feishu-bridge/sessions.json`` with ``"enabled": true``. Delivery is
best effort: failures are logged and never break the speak tool result or the
turn.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OPEN_BASE = "https://open.feishu.cn"
# v2 OAuth with the client_credentials grant returns the app access token
# (v1 /authen/v1/tenant_access_token/internal is not served on this edge).
TOKEN_URL = f"{OPEN_BASE}/open-apis/authen/v2/oauth/token"
MESSAGE_URL = f"{OPEN_BASE}/open-apis/im/v1/messages"
IMAGE_UPLOAD_URL = f"{OPEN_BASE}/open-apis/im/v1/images"
FILE_UPLOAD_URL = f"{OPEN_BASE}/open-apis/im/v1/files"
# Image messages need an uploaded image_key; everything else rides the file
# upload + file message pair.
IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tif", ".tiff"}
)
REQUEST_TIMEOUT_SECONDS = 10.0
# 失效/缺失 token 的错误码（HTTP 200 业务码或 HTTP 4xx JSON body 均可能出现）：
# 刷新一次 token 后重试
TOKEN_ERROR_CODES = {99991661, 99991663, 99991668, 99991679}
TOKEN_EXPIRY_BUFFER_SECONDS = 60.0

_logger = logging.getLogger(__name__)


def _read_json_response(request: urllib.request.Request) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        # Feishu reports auth problems as HTTP 4xx with a JSON code body;
        # surface that body so the code-based refresh logic can react.
        return json.loads(error.read().decode("utf-8"))


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
    return _read_json_response(request)


def _post_multipart(
    url: str,
    fields: dict[str, str],
    field_name: str,
    path: Path,
    *,
    token: str | None = None,
) -> dict[str, Any]:
    """Upload one file as multipart/form-data with an ASCII-safe filename."""

    boundary = f"----lara{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        field_header = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
        )
        chunks.append(
            field_header.encode("utf-8") + value.encode("utf-8") + b"\r\n"
        )
    ascii_name = path.name.encode("ascii", "replace").decode("ascii")
    disposition = f'name="{field_name}"; filename="{ascii_name}"'
    if ascii_name != path.name:
        # http.client encodes header values as latin-1; keep the real name in
        # the RFC 5987 parameter instead of the raw header text.
        disposition += f"; filename*=UTF-8''{urllib.parse.quote(path.name)}"
    file_header = (
        f"--{boundary}\r\nContent-Disposition: form-data; {disposition}"
        "\r\nContent-Type: application/octet-stream\r\n\r\n"
    )
    chunks.append(file_header.encode("ascii") + path.read_bytes() + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url, data=b"".join(chunks), headers=headers, method="POST"
    )
    return _read_json_response(request)


class FeishuChannel:
    """Delivers speak messages to one Feishu chat via a tenant-access token."""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        chat_id: str,
        post: Callable[..., dict[str, Any]] = _post_json,
        upload: Callable[..., dict[str, Any]] = _post_multipart,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.chat_id = chat_id
        self._post = post
        self._upload = upload
        self._monotonic = monotonic
        self._token_lock = threading.Lock()
        self._token_value: str | None = None
        self._token_expires_at = 0.0

    @property
    def recipient_id(self) -> str:
        return f"feishu:{self.app_id}:{self.chat_id}"

    def deliver_with_id(
        self, reply_id: str, text: str, attachments: Sequence[Path] = ()
    ) -> bool:
        return self.deliver(text, attachments, reply_id=reply_id)

    def deliver(
        self, text: str, attachments: Sequence[Path] = (), *, reply_id: str | None = None
    ) -> bool:
        """Send the text and files; never raises — the channel must not break turns."""

        try:
            if text:
                self._deliver(text, self._message_uuid(reply_id, "text"))
            for index, path in enumerate(attachments):
                self._deliver_attachment(
                    path, self._message_uuid(reply_id, f"attachment:{index}")
                )
            return True
        except Exception:  # noqa: BLE001 - best-effort delivery
            _logger.warning("feishu channel delivery failed", exc_info=True)
            return False

    def _message_uuid(self, reply_id: str | None, part: str) -> str | None:
        if reply_id is None:
            return None
        return uuid.uuid5(uuid.NAMESPACE_URL, f"{self.recipient_id}:{reply_id}:{part}").hex

    def _deliver(self, text: str, message_uuid: str | None = None) -> None:
        self._send_message("text", {"text": text}, message_uuid)

    def _deliver_attachment(self, path: Path, message_uuid: str | None) -> None:
        if path.suffix.lower() in IMAGE_SUFFIXES:
            key = self._uploaded_key(
                IMAGE_UPLOAD_URL, {"image_type": "message"}, "image", path, "image_key"
            )
            self._send_message("image", {"image_key": key}, message_uuid)
            return
        key = self._uploaded_key(
            FILE_UPLOAD_URL,
            {"file_type": "stream", "file_name": path.name},
            "file",
            path,
            "file_key",
        )
        self._send_message("file", {"file_key": key}, message_uuid)

    def _with_token_refresh(
        self, call: Callable[[str | None], dict[str, Any]]
    ) -> dict[str, Any]:
        """Run one API call, refreshing the cached token once when rejected."""

        response = call(self._token())
        if response.get("code") in TOKEN_ERROR_CODES:
            response = call(self._token(force=True))
        return response

    def _send_message(
        self, msg_type: str, content: dict[str, Any], message_uuid: str | None
    ) -> None:
        payload = {
            "receive_id": self.chat_id,
            "msg_type": msg_type,
            "content": json.dumps(content, ensure_ascii=False),
        }
        if message_uuid is not None:
            payload["uuid"] = message_uuid
        url = f"{MESSAGE_URL}?receive_id_type=chat_id"
        response = self._with_token_refresh(
            lambda token: self._post(url, payload, token=token)
        )
        if response.get("code") != 0:
            raise RuntimeError(
                f"feishu message error code={response.get('code')}: {response.get('msg')}"
            )

    def _uploaded_key(
        self,
        url: str,
        fields: dict[str, str],
        field_name: str,
        path: Path,
        key_name: str,
    ) -> str:
        response = self._with_token_refresh(
            lambda token: self._upload(url, fields, field_name, path, token=token)
        )
        if response.get("code") != 0:
            raise RuntimeError(
                f"feishu upload error code={response.get('code')}: {response.get('msg')}"
            )
        data = response.get("data")
        key = data.get(key_name) if isinstance(data, dict) else None
        if not isinstance(key, str) or not key:
            raise RuntimeError(f"feishu upload response has no {key_name}")
        return key

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
