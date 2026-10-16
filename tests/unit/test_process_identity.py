from __future__ import annotations

import os
import subprocess
import sys

from lara.core.process import current_owner, owner_is_foreign_and_alive


def test_owner_stamp_identifies_the_current_process() -> None:
    owner = current_owner()
    assert owner["pid"] == os.getpid()
    assert owner["process_token"]
    assert owner_is_foreign_and_alive(owner) is False


def test_malformed_or_missing_stamps_are_not_foreign() -> None:
    assert owner_is_foreign_and_alive(None) is False
    assert owner_is_foreign_and_alive({}) is False
    assert owner_is_foreign_and_alive({"pid": "x", "process_token": "t"}) is False


def test_live_foreign_process_counts_as_foreign() -> None:
    owner = {"pid": os.getpid(), "process_token": "another-host"}
    assert owner_is_foreign_and_alive(owner) is True


def test_dead_owner_process_is_not_foreign_alive() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    owner = {"pid": proc.pid, "process_token": "another-host"}
    assert owner_is_foreign_and_alive(owner) is False
