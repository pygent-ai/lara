"""Cross-process ownership identity for artifacts shared by several hosts.

Lara hosts (CLI, desktop API) share one project's sessions root, so a case run
created by one process is visible to the others. The owner stamp lets a host
tell "the run's own process is gone" apart from "the run lives in another live
process" before it reconciles metadata on the owner's behalf.
"""

from __future__ import annotations

import ctypes
import os
import uuid
from typing import Any

from lara.core.io import utc_now

# Stable for the lifetime of this process; a restarted host gets a new token.
PROCESS_TOKEN = uuid.uuid4().hex


def current_owner() -> dict[str, Any]:
    return {
        "pid": os.getpid(),
        "process_token": PROCESS_TOKEN,
        "recorded_at": utc_now(),
    }


def owner_is_foreign_and_alive(owner: Any) -> bool:
    """Report whether `owner` is another process that is still running.

    Unknown or malformed stamps never count as foreign: legacy metadata and
    foreign finalize calls must keep today's behavior rather than gaining a
    new failure mode. A same-process stamp is never foreign. PID reuse can
    only bias this towards "alive", which keeps the owner's terminal authoritative.
    """

    if not isinstance(owner, dict):
        return False
    pid = owner.get("pid")
    token = owner.get("process_token")
    if not isinstance(pid, int) or pid <= 0 or not isinstance(token, str):
        return False
    if token == PROCESS_TOKEN:
        return False
    return _pid_alive(pid)


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        ERROR_INVALID_PARAMETER = 87
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            if ctypes.get_last_error() == ERROR_INVALID_PARAMETER:
                # No such process: the owner is definitively gone.
                return False
            # Access denied or unknown: assume alive so a foreign owner is
            # never declared dead on inconclusive evidence.
            return True
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        # PermissionError and platform quirks: inconclusive means alive.
        return True
    return True


__all__ = ["PROCESS_TOKEN", "current_owner", "owner_is_foreign_and_alive"]
