"""End-to-end check for the dsh-bridge: DSH plugin -> bridge -> pygent MCP.

Spawns the bridge with the demo DSH plugin through the same transport the lora
runtime uses (`lara.runtime.mcp.ReadinessRetryingTransport` wrapping pygent's
`MCPStdioTransport`), discovers tools through `MCPClientPool` exactly like
`lara.runtime.service` does, then exercises the happy path, the argument-
validation path, the tool-error path, and the unknown-tool path.

Run from the repository root:  uv run python scripts/dsh-bridge/verify.py
Requires Node >= 18.17 and `npm install` inside scripts/dsh-bridge.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, cast

from pygent.lara.runtime.mcp import ReadinessRetryingTransport
from pygent.tool.mcp import MCPClientPool

BRIDGE_DIR = Path(__file__).resolve().parent
PLUGINS = (
    "demo-plugin/demo-dsh-tools-plugin.mjs",
    "demo-plugin/legacy-style-plugin.mjs",
)
TOOL_NAME = "dsh_demo_word_stats"
LEGACY_TOOL_NAME = "dsh_legacy_echo"


def _text(result: object) -> str:
    content = getattr(result, "content", [])
    return "\n".join(
        item.text for item in content if getattr(item, "text", None)
    )


async def main() -> int:
    transport = ReadinessRetryingTransport(
        "node",
        args=("bridge.mjs", *PLUGINS),
        cwd=BRIDGE_DIR,
    )
    pool = MCPClientPool({"dsh-bridge": transport}, version="1")
    await pool.connect()
    try:
        entries = pool.executors()
        names = {spec.definition.name for spec, _ in entries}
        assert TOOL_NAME in names, f"demo tool missing from discovery: {sorted(names)}"
        spec = next(spec for spec, _ in entries if spec.definition.name == TOOL_NAME)
        schema = cast(dict[str, Any], dict(spec.definition.parameters))
        assert schema.get("type") == "object", schema
        assert set(cast(list[str], schema.get("required", ()))) == {"text"}, schema
        properties = cast(dict[str, Any], schema.get("properties") or {})
        assert "upperLimit" in properties, schema
        print(f"PASS discovery: {TOOL_NAME} with required={schema.get('required')}")

        async with transport.session() as session:
            result = await session.call_tool(
                TOOL_NAME, arguments={"text": "hello dsh bridge world"}
            )
            assert not result.is_error, _text(result)
            assert "words=4" in _text(result), _text(result)
            assert "characters=22" in _text(result), _text(result)
            print(f"PASS happy path: {_text(result)!r}")

            result = await session.call_tool(
                TOOL_NAME,
                arguments={"text": "one two three four five six", "upperLimit": 4},
            )
            assert not result.is_error, _text(result)
            assert "words=6" in _text(result) and "(over limit)" in _text(result), _text(result)
            print(f"PASS optional param + render flag: {_text(result)!r}")

            result = await session.call_tool(
                TOOL_NAME, arguments={"upperLimit": 3}
            )
            assert result.is_error, _text(result)
            assert "invalid arguments" in _text(result), _text(result)
            print(f"PASS argument validation rejected: {_text(result)!r}")

            result = await session.call_tool(
                TOOL_NAME, arguments={"text": "x", "explode": True}
            )
            assert result.is_error, _text(result)
            assert "explode=true" in _text(result), _text(result)
            print(f"PASS tool error surfaced: {_text(result)!r}")

            result = await session.call_tool("no_such_tool", arguments={})
            assert result.is_error, _text(result)
            assert "unknown tool" in _text(result), _text(result)
            print("PASS unknown tool rejected")

            result = await session.call_tool(
                LEGACY_TOOL_NAME,
                arguments={"message": "hi", "prefix": "dsh"},
            )
            assert not result.is_error, _text(result)
            assert _text(result) == "dsh: hi", _text(result)
            print(f"PASS hand-rolled plugin (raw spec path): {_text(result)!r}")

            result = await session.call_tool(
                LEGACY_TOOL_NAME, arguments={"prefix": "dsh"}
            )
            assert result.is_error, _text(result)
            assert "invalid arguments" in _text(result), _text(result)
            print("PASS hand-rolled plugin argument validation rejected")
    finally:
        await pool.aclose()
    print("dsh-bridge verify: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
