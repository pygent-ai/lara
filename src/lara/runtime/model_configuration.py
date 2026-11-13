"""lora 产品层补充；基础实现全部来自 SDK ``pygent.agent.pygent_v1.runtime.model_configuration``。"""

from __future__ import annotations


import os
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
from pygent import MediaSource
from pygent.core import FrozenJsonObject
from pygent.llm import (
    AnthropicMessagesAdapter,
    AnthropicMessagesClient,
    ModelProviderRequest,
    CapabilityPresetCatalog,
    GeminiGenerateContentAdapter,
    GeminiGenerateContentClient,
    ModelCapabilities,
    ModelCapabilityCatalog,
    ModelConfig,
    ConnectionConfig,
    MediaTransportCapabilities,
    ModelEntry,
    ModelInfo,
    ModelLimits,
    ModelProviderClient,
    ModelSpec,
    OpenAICompatibleClient,
    OpenAICompatibleAdapter,
    OpenAIResponsesAdapter,
    OpenAIResponsesClient,
    ProviderCatalog,
    ResolvedModelConnection,
)

from lara.credentials import list_user_credential_names, lookup_credential

from pygent.agent.pygent_v1.runtime.attachments import MEDIA_MAX_BYTES
from pygent.agent.pygent_v1.graph.model_invoker import PygentModelInvoker

import pygent.agent.pygent_v1.runtime.model_configuration as _sdk_model_configuration
from pygent.agent.pygent_v1.runtime.model_configuration import (  # noqa: F401
    CLIENT_FACTORIES,
    CredentialEnvironment,
    build_model_invoker,
    preferred_models,
    preferred_profile_name,
    workspace_media_resolver,
    _create_client,
)


# Anthropic Messages 的 ``max_tokens`` 是必填字段,而 agent 链路构造的
# ``GenerationConfig`` 不带 ``max_output_tokens``,导致走该协议的模型在
# build_request 阶段就被拒绝。这里把模型声明的 ``limits.max_output_tokens``
# 作为请求默认值补上;模型未声明上限时回退到 Anthropic 文档中的通用下限。
DEFAULT_ANTHROPIC_MAX_OUTPUT_TOKENS = 4096


class LaraAnthropicMessagesAdapter(AnthropicMessagesAdapter):
    def build_request(self, request: ModelProviderRequest) -> FrozenJsonObject:
        if request.generation.max_output_tokens is None:
            limit = request.model.capabilities.limits.max_output_tokens
            request = replace(
                request,
                generation=replace(
                    request.generation,
                    max_output_tokens=limit or DEFAULT_ANTHROPIC_MAX_OUTPUT_TOKENS,
                ),
            )
        return super().build_request(request)


# SDK 的 build_model_invoker 在模块内按名字引用 AnthropicMessagesAdapter,
# 替换该绑定即可让 PygentAgent 自建的 invoker 也走补默认值的适配器。
_sdk_model_configuration.AnthropicMessagesAdapter = LaraAnthropicMessagesAdapter


def builtin_model_catalogs() -> dict[str, Any]:
    providers = {}
    for key, provider in ProviderCatalog.builtin().providers.items():
        providers[key] = {
            "provider": provider.provider,
            "display_name": provider.display_name,
            "default_protocol": provider.default_protocol,
            "protocols": {
                protocol_key: {
                    "protocol": protocol.protocol,
                    "base_url": protocol.base_url,
                    "authentication": protocol.authentication,
                    "api_key_env": protocol.api_key_env,
                    "provider_options_schema": protocol.provider_options_schema.to_dict(),
                }
                for protocol_key, protocol in provider.protocols.items()
            },
        }
    capabilities = [
        {
            "provider": provider,
            "model_id": model_id,
            "protocol": protocol,
            "capabilities": value.to_mapping(),
        }
        for (provider, model_id, protocol), value in ModelCapabilityCatalog.builtin().models.items()
    ]
    presets = {}
    for key, preset in CapabilityPresetCatalog.builtin().presets.items():
        presets[key] = ModelCapabilities(
            modalities=preset.modalities,
            streaming=preset.streaming,
            tools=preset.tools,
            structured_output=preset.structured_output,
            reasoning=preset.reasoning,
            limits=ModelLimits(context_tokens=None, max_output_tokens=None),
        ).to_mapping()
    return {
        "providers": providers,
        "model_capabilities": capabilities,
        "capability_presets": presets,
    }


async def discover_models(
    *,
    connection_name: str,
    protocol: str,
    connection: ConnectionConfig,
    credential_environ: Mapping[str, str],
    timeout: float = 10.0,
) -> tuple[ModelInfo, ...]:
    client = _create_client(
        connection=ResolvedModelConnection(
            connection_key=connection_name,
            provider=connection.provider,
            protocol=protocol,
            base_url=connection.protocols[protocol],
            credential=connection.credential,
            verify_ssl=connection.verify_ssl,
            proxy=connection.proxy,
        ),
        credential_environ=credential_environ,
    )
    try:
        catalog = getattr(client, "models", None)
        if catalog is None:
            raise ValueError(
                f"protocol {protocol!r} does not provide native model discovery"
            )
        return await catalog.list(timeout=timeout)
    finally:
        await client.aclose()


def lara_credential_environment(
    user_pygent_root: str | Path,
    *,
    transient: Mapping[str, str] | None = None,
) -> CredentialEnvironment:
    """SDK ``CredentialEnvironment`` backed by lora's credential store.

    The SDK kernel only reads the process environment; lora adds the user
    ``credentials.env`` file and the OS keyring through the injected lookup.
    """

    from lara.credentials import list_user_credential_names, lookup_credential

    return CredentialEnvironment(
        user_pygent_root,
        transient=transient,
        lookup=lambda name, root: lookup_credential(name, user_pygent_root=root),
        name_lister=list_user_credential_names,
    )
