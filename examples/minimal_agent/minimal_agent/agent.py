"""Minimal standalone ReAct agent extracted from Lara's agent runtime.

This module is the smallest faithful slice of ``src/lara/runtime/agent``.
It keeps the core execution stack and drops all Lara-specific durability
(session checkpoints, trace events, approvals, reminders, context
compression, dynamic prompt registry).

Lara stack                        Minimal equivalent
--------------------------------  -------------------------------------------------
LaraAgent.forward()               MinimalAgent.forward()
DynamicPromptModule               SYSTEM_PROMPT (plain string)
LaraForegroundAgent (PygentAgent) ReActLayer
ForegroundModelModule             ModelCallLayer
LaraModelInvoker                  DefaultModelInvoker
PreparedToolModule                ToolCallLayer (toolkit.local_layer)
StandardTools / ToolKit           StandardTools / toolkit (bash + files)
LaraToolAuthorization             AllowAllAuthorization
"""

from __future__ import annotations

import os
from pathlib import Path

from pygent import (
    AIMessage,
    CapabilityPresetCatalog,
    Context,
    GenerationConfig,
    ModelCallLayer,
    ModelEntry,
    ModelGroup,
    ModelSpec,
    Module,
    ReActLayer,
    RetryPolicy,
    ToolAuthorizationDecision,
    ToolAuthorizationRequest,
    UserMessage,
)
from pygent.llm import (
    DefaultModelInvoker,
    OpenAICompatibleAdapter,
    OpenAICompatibleClient,
)
from pygent.tool import StandardTools

SYSTEM_PROMPT = """\
You are a minimal coding agent.

You work in a workspace and have access to tools:
- bash: run a shell command (use for builds, tests, git, and anything not
  covered by the file tools)
- read / write / edit / glob / grep: inspect and modify files

Rules:
- Read before you modify; verify after you modify.
- Prefer the narrowest tool that gets the job done.
- Stop as soon as the task is complete; give a concise final answer.
"""


class AllowAllAuthorization(Module[ToolAuthorizationRequest, ToolAuthorizationDecision]):
    """Replaces LaraToolAuthorization: every tool call is allowed."""

    async def forward(
        self, request: ToolAuthorizationRequest, context: Context
    ) -> tuple[ToolAuthorizationDecision, Context]:
        return (
            ToolAuthorizationDecision(
                call_id=request.call.call_id,
                allowed=True,
                reason_code="allow_all",
            ),
            context,
        )


class MinimalAgent(Module[UserMessage, AIMessage]):
    """Replaces LaraAgent: prompt -> ReAct loop, nothing else."""

    def __init__(self, react: ReActLayer) -> None:
        super().__init__()
        self.react = react

    async def forward(
        self, message: UserMessage, context: Context
    ) -> tuple[AIMessage, Context]:
        return await self.react(message, context)


def build_agent(
    *,
    workspace_root: str | Path,
    base_url: str | None = None,
    api_key: str | None = None,
    model_id: str | None = None,
    system_prompt: str = SYSTEM_PROMPT,
    max_steps: int = 8,
    max_model_calls: int = 8,
    max_tool_calls: int = 32,
) -> tuple[MinimalAgent, DefaultModelInvoker, Context]:
    """Assemble the agent graph plus the resources the caller must close.

    Mirrors LaraAgent.__init__ -> _assemble_definition, minus the managed
    execution layers (checkpoint/audit/reminders/persisted diff).
    """
    base_url = base_url or os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
    api_key = api_key or os.environ.get("LLM_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "LLM_API_KEY is missing; set it in the environment or --api-key"
        )
    model_id = model_id or os.environ.get("LLM_MODEL", "gpt-4o-mini")
    model_name = os.environ.get("LLM_PROVIDER", "openai")
    workspace_root = Path(workspace_root)

    # Tools: the same StandardTools/ToolKit Lara registers in
    # LaraAgent._register_default_tools (bash + task get/stop + files).
    toolkit = StandardTools(workspace_root=workspace_root).toolkit

    # Model: DefaultModelInvoker (Lara's LaraModelInvoker is a thin subclass)
    # plus one OpenAI-compatible client. Point LLM_BASE_URL at any
    # OpenAI-compatible endpoint (DeepSeek, vLLM, Ollama, ...).
    invoker = DefaultModelInvoker(
        adapters={"openai_chat_completions": OpenAICompatibleAdapter()},
        clients={
            "primary": OpenAICompatibleClient(
                base_url=base_url,
                api_key=api_key,
            )
        },
    )
    model = ModelCallLayer(
        model_group=ModelGroup(
            name="minimal-agent",
            models=(
                ModelEntry(
                    "primary",
                    ModelSpec(
                        provider=model_name,
                        model_id=model_id,
                        protocol="openai_chat_completions",
                        capabilities=CapabilityPresetCatalog.builtin().presets[
                            "text_tools_structured_reasoning"
                        ].materialize(context_tokens=128_000, max_output_tokens=8192),
                    ),
                ),
            ),
        ),
        retry_policy=RetryPolicy(
            max_attempts_per_model=2,
            attempt_idle_timeout_seconds=60.0,
        ),
        generation=GenerationConfig(temperature=0.1, max_output_tokens=2048),
        tools=toolkit.definitions,
        invoker=invoker,
    )

    # Tool layer: the authorization module is the only policy hook; Lara adds
    # audit/reminders/persisted-diff wrappers around this same layer.
    tool_layer = toolkit.local_layer(
        authorization=AllowAllAuthorization(),
        max_concurrency=3,
    )

    agent = MinimalAgent(
        ReActLayer(
            model=model,
            tools=tool_layer,
            max_steps=max_steps,
            max_model_calls=max_model_calls,
            max_tool_calls=max_tool_calls,
        )
    )

    # Lara's DynamicPromptModule injects a big generated system prompt; here
    # the system prompt is fixed and the tools are made visible to the model.
    context = toolkit.make_visible_in(
        Context(
            system_prompt=system_prompt,
            metadata={},
        )
    )
    return agent, invoker, context
