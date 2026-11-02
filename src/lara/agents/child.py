"""Independent child agents for the LARA architecture.

A child agent owns its system prompt and starts from a fresh context — no
inherited history. It runs through the same :class:`AgentRunner` seam as the
main and fork agents, referencing registered tool surfaces via
:class:`~lara.agents.runner.ToolProfile` (lara-architecture G2: two
background origins, no privilege difference, no side-channel tools).
"""

from __future__ import annotations

from dataclasses import dataclass

from .runner import AgentLimits, AgentRunResult, AgentRunner, ToolProfile


@dataclass(frozen=True)
class ChildSpec:
    """Declarative description of one independent background agent.

    ``tools`` may override the runner's default profile, but the override is
    still a :class:`ToolProfile` reference resolved through the registry —
    custom tools enter through the uniform mechanism, never as modules.
    """

    name: str
    system_prompt: str
    tools: ToolProfile | None = None
    limits: AgentLimits | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("ChildSpec.name must be non-empty")
        if not self.system_prompt.strip():
            raise ValueError("ChildSpec.system_prompt must be non-empty")


class ChildAgent:
    """An independent background agent with its own context."""

    def __init__(
        self,
        runner: AgentRunner,
        spec: ChildSpec,
        *,
        label: str | None = None,
    ) -> None:
        self._runner = runner
        self._spec = spec
        self._label = label or f"lara-child.{spec.name}"

    async def run(self, directive: str) -> AgentRunResult:
        """Run the child agent against its directive."""

        return await self._runner.run(
            message=directive,
            system_prompt=self._spec.system_prompt,
            tools=self._spec.tools,
            limits=self._spec.limits,
            label=self._label,
        )
