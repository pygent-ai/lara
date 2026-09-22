# Minimal Agent

A minimal, standalone ReAct agent extracted from Lara's agent runtime
(`src/lara/runtime/agent/`). It runs on its own: the only dependency is
`pygent-ai` (the same framework Lara builds on); it does **not** import the
`lara` package at all.

## What it keeps vs. what it drops

Lara's `LaraAgent` is a pygent ReAct agent wrapped in a lot of Lara-specific
modules. This project keeps the core loop and deletes everything else.

| Lara implementation | Minimal equivalent |
| --- | --- |
| `LaraAgent.forward()` (`core.py`) | `MinimalAgent.forward()` (`agent.py`) |
| `DynamicPromptModule` + `PromptRegistry` (`prompts.py`, `prompt_sources.py`) | a plain `SYSTEM_PROMPT` string |
| `LaraForegroundAgent` / `PygentAgent` (ReAct + compression) | `ReActLayer` |
| `ForegroundModelModule` + `ModelCallLayer` (`pipeline.py`) | `ModelCallLayer` |
| `LaraModelInvoker` (`model_invoker.py`) | `DefaultModelInvoker` |
| `PreparedToolModule` + `ToolCallLayer` + `StandardTools` | `toolkit.local_layer(...)` |
| `LaraToolAuthorization` (`pipeline.py`) | `AllowAllAuthorization` |
| session checkpoints / trace events / reminders / persisted diff / repeated-call guard / context compression | dropped |

## Layout

```
minimal_agent/
├── pyproject.toml       # standalone project; only depends on pygent-ai
├── minimal_agent/
│   ├── agent.py         # tools + model layer + ReAct loop
│   └── cli.py           # one-shot and REPL entry
└── README.md
```

## Run

```bash
cd examples/minimal_agent
python -m venv .venv
# Windows: .venv\Scripts\activate   macOS/Linux: source .venv/bin/activate
pip install -e .

# Configure the model endpoint (OpenAI-compatible)
export LLM_BASE_URL=https://api.openai.com/v1   # or DeepSeek/vLLM/Ollama
export LLM_API_KEY=sk-...
export LLM_MODEL=gpt-4o-mini

# One-shot
python -m minimal_agent.cli "列出当前目录并总结"

# Interactive (conversation state persists across lines)
python -m minimal_agent.cli --repl
```

Environment variables: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`,
`LLM_PROVIDER` (defaults: OpenAI endpoint, `gpt-4o-mini`).

## How to extend it back toward Lara

- **System prompt**: replace `SYSTEM_PROMPT` with a function of the request;
  Lara's `DynamicPromptModule` does this with a registry of prompt modules.
- **Tool policy**: replace `AllowAllAuthorization` with an approval policy —
  this is exactly where Lara plugs `LaraToolAuthorization` and interactive
  approvals.
- **Durability**: Lara persists every user/model/tool boundary into
  `session.json` (`ConversationCheckpointModelModule/ToolModule`) and replays
  effects through the pygent managed-execution layer. The minimal version
  keeps conversation state only in memory.
- **More tools**: `StandardTools` already bundles bash, file edit/read/write/
  glob/grep, web fetch/search and notebook tools; `ToolKit(...)` can assemble
  any subset.
