"""Entry point for the standalone single-file lara.exe build.

Double-clicking the exe starts an interactive chat session so the file is
usable without a terminal command line; terminal users still get the full CLI
by passing arguments. The desktop bundle keeps using ``lara_entry.py`` so its
behavior stays unchanged.
"""

from __future__ import annotations

import sys

from lara.cli import main

_DOUBLE_CLICK_DEFAULT_ARGS = ["session", "chat", "--new"]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or _DOUBLE_CLICK_DEFAULT_ARGS))
