"""lora 产品层补充；基础实现全部来自 SDK ``pygent.agent.pygent_v1.core.io``。"""

from __future__ import annotations


import hashlib
import inspect
import json
import os
import threading
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pygent.agent.pygent_v1.core.io import *  # noqa: F401,F403


def file_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "content_hash": None, "size": None}
    data = path.read_bytes()
    return {"exists": True, "content_hash": hashlib.sha256(data).hexdigest(), "size": len(data)}


def read_jsonl_snapshot(path: str | Path) -> str:
    """Read a JSONL file while excluding in-process appenders."""

    jsonl_path = Path(path)
    with jsonl_path_lock(jsonl_path):
        if not jsonl_path.exists():
            return ""
        return jsonl_path.read_text(encoding="utf-8")


