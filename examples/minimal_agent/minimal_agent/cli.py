"""CLI entry for the minimal standalone agent.

One-shot mode::

    python -m minimal_agent.cli "list this directory and summarize it"

Interactive mode (one ReAct turn per line, conversation state is kept)::

    python -m minimal_agent.cli --repl
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from pygent import UserMessage
from pygent.llm import ModelEventKind

from .agent import build_agent

DEFAULT_WORKSPACE = Path.cwd()


async def run_turn(agent, context, text: str) -> tuple[str, object]:
    """One full ReAct turn; returns (final_answer, next_context)."""
    async with agent.stream(UserMessage(content=text), context) as stream:
        async for event in stream:
            if event.kind == ModelEventKind.TEXT_DELTA.value:
                print(event.data.get("text", ""), end="", flush=True)
            elif event.kind == "tool.started":
                data = dict(event.data)
                print(f"\n[tool] {data.get('name', '?')} {data.get('call_id', '')}", flush=True)
            elif event.kind == "tool.completed":
                data = dict(event.data)
                print(f"\n[tool done] {data.get('name', '?')} rc={data.get('exit_code', '?')}", flush=True)
        answer, next_context = await stream.final_result()
    print()
    return answer.content, next_context


async def main_async(args: argparse.Namespace) -> None:
    agent, invoker, context = build_agent(workspace_root=args.workspace)
    try:
        if args.repl:
            print("minimal-agent REPL. Type your request; Ctrl-D / Ctrl-C to exit.")
            while True:
                text = input("\n> ").strip()
                if not text:
                    continue
                answer, context = await run_turn(agent, context, text)
                print(f"\n{answer}")
        else:
            text = args.prompt or "请简单介绍你自己，并说明你有哪些工具。"
            answer, context = await run_turn(agent, context, text)
            print(f"\n{answer}")
    finally:
        await invoker.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal standalone ReAct agent")
    parser.add_argument("prompt", nargs="?", default=None, help="one-shot prompt")
    parser.add_argument("--repl", action="store_true", help="interactive REPL mode")
    parser.add_argument(
        "--workspace",
        default=str(DEFAULT_WORKSPACE),
        help="workspace root the agent operates on (default: cwd)",
    )
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible base URL")
    parser.add_argument("--api-key", default=None, help="API key (env: LLM_API_KEY)")
    parser.add_argument("--model", default=None, help="model id (env: LLM_MODEL)")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
