"""On-demand session compression root (lora-only; the SDK runs compression inline)."""

from __future__ import annotations

from pygent import AIMessage, Module, UserMessage
from pygent.core import EffectSafety, ExecutionRequirements, RecoverySafety

from pygent.agent.pygent_v1.graph.core import PygentAgent
from pygent.agent.pygent_v1.runtime.context import PygentContext as LaraContext


class SessionCompressionModule(Module[UserMessage, AIMessage]):
    """Root execution that runs one on-demand compression round.

    Holding the agent keeps the compressor in the bound tree, so its model
    call runs as a Child of this execution and stays journaled and evented
    like every other model job.
    """

    execution_requirements = ExecutionRequirements(
        recovery_safety=RecoverySafety.MODULE_BOUNDARY_RETRY,
        effect_safety=EffectSafety.MANAGED_EFFECTS,
    )
    trusted_live_resource_attributes = ("agent",)

    def __init__(self, agent: PygentAgent) -> None:
        super().__init__()
        self.agent = agent

    async def forward(
        self,
        message: UserMessage,
        context: LaraContext,
    ) -> tuple[AIMessage, LaraContext]:
        compressed = await self.agent.foreground.compress(context)
        return AIMessage(
            content=(
                f"context compressed: {len(context.messages)} messages became "
                f"{len(compressed.messages)} (compression "
                f"{compressed.compression_count})"
            )
        ), compressed


__all__ = ["SessionCompressionModule"]
