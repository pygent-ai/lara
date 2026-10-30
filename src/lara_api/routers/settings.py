from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pygent.llm import ConnectionConfig, EnabledModelConfig, ModelGroupConfig

from lara.config import replace_user_model_config, update_user_approvals
from lara.core.io import non_empty_string
from lara.credentials import set_user_credential
from lara.runtime.model_configuration import (
    CredentialEnvironment,
    builtin_model_catalogs,
    discover_models,
)
from lara.core.io import read_json
from lara_api.dependencies import ApiContext, get_api_context
from lara_api.models.requests import DiscoverModelsRequest, UpdateSettingsRequest
from lara_api.models.responses import RuntimeConfigResponse
from lara_api.services.project_service import config_response

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=RuntimeConfigResponse)
def get_settings(
    context: ApiContext = Depends(get_api_context),
) -> RuntimeConfigResponse:
    return config_response(context.config)


@router.patch("", response_model=RuntimeConfigResponse)
async def update_settings(
    request: UpdateSettingsRequest,
    context: ApiContext = Depends(get_api_context),
) -> RuntimeConfigResponse:
    overrides = _settings_overrides(request)
    if overrides.get("workspace_root"):
        workspace = Path(overrides["workspace_root"]).expanduser()
        if not workspace.is_dir():
            raise HTTPException(
                status_code=400,
                detail="Project folder does not exist or is not accessible. Choose an existing folder.",
            )
    user_lara_root = context.config.user_lara_root or ""
    agent_alias = str(
        overrides.get("agent_alias", context.agent_alias or context.config.agent_alias)
    )
    if request.native_model_config is not None:
        _replace_native_model_settings(
            request,
            user_lara_root=user_lara_root,
            agent_alias=agent_alias,
            context=context,
        )
    elif request.model_key_renames:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_model_configuration",
                "path": "model_config",
                "message": "model_key_renames requires model_config",
            },
        )
    if request.approvals_enabled is not None:
        update_user_approvals(user_lara_root, enabled=request.approvals_enabled)
    config = await context.areload(overrides)
    context.remember_project(config.workspace_root)
    return config_response(config)


@router.get("/model-catalogs")
def get_model_catalogs() -> dict[str, Any]:
    return builtin_model_catalogs()


@router.post("/models/discover")
async def discover_connection_models(
    request: DiscoverModelsRequest,
    context: ApiContext = Depends(get_api_context),
) -> dict[str, Any]:
    try:
        connection = ConnectionConfig.from_mapping(request.connection)
        credential = request.connection.get("credential")
        env_name = credential.get("env") if isinstance(credential, dict) else None
        transient = (
            {env_name: request.credential_value}
            if isinstance(env_name, str) and request.credential_value
            else {}
        )
        models = await discover_models(
            connection_name="discovery",
            protocol=request.protocol,
            connection=connection,
            credential_environ=CredentialEnvironment(
                context.config.user_lara_root or "", transient=transient
            ),
            timeout=request.timeout_seconds,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "model_discovery_failed", "message": str(exc)},
        ) from exc
    return {
        "models": [
            {"id": item.id, "created": item.created, "owned_by": item.owned_by}
            for item in models
        ]
    }


def _replace_native_model_settings(
    request: UpdateSettingsRequest,
    *,
    user_lara_root: str,
    agent_alias: str,
    context: ApiContext,
) -> None:
    assert request.native_model_config is not None
    try:
        native = request.native_model_config
        unknown_fields = set(native) - {"connections", "models", "model_groups"}
        if unknown_fields:
            raise ValueError(
                "unknown model config fields: "
                + ", ".join(sorted(unknown_fields))
            )
        missing_fields = {"connections", "models"} - set(native)
        if missing_fields:
            raise ValueError(
                "missing model config fields: "
                + ", ".join(sorted(missing_fields))
            )
        connections = native.get("connections") or {}
        models = native.get("models") or {}
        groups = native.get("model_groups") or {}
        if (
            not isinstance(connections, dict)
            or not isinstance(models, dict)
            or not isinstance(groups, dict)
        ):
            raise ValueError(
                "model_config must map connections, models and model_groups"
            )
        group_models = _validate_native_model_config(
            connections, models, groups
        )
        renames = _validate_model_key_renames(request, context, models)
        group_name = non_empty_string(request.default_model_group)
        if groups and (group_name is None or group_name not in groups):
            raise ValueError(
                f"default_model_group references unknown model group {group_name!r}"
            )
        _guard_persisted_session_selections(context, group_models, renames)
        referenced_credentials = {
            credential["env"]
            for connection in connections.values()
            if isinstance(connection, dict)
            and isinstance((credential := connection.get("credential")), dict)
            and isinstance(credential.get("env"), str)
        }
        unknown = set(request.credential_values) - referenced_credentials
        if unknown:
            raise ValueError(
                "credential_values references unknown environment variables: "
                + ", ".join(sorted(unknown))
            )
        retry = (
            request.retry.model_dump()
            if request.retry is not None
            else {"max_attempts_per_model": 2}
        )
        model_request: dict[str, Any] = {"retry": retry}
        if groups:
            model_request = {"default_model_group": group_name, "retry": retry}
        agents = [
            {
                "alias": agent_alias,
                "model_request": model_request,
            }
        ]
        for env_name, value in request.credential_values.items():
            secret = non_empty_string(value)
            if secret:
                set_user_credential(user_lara_root, env_name, secret)
        # Migrate before the config replacement: sessions then never reference
        # keys that are missing from whichever configuration is on disk.
        _migrate_model_key_renames(context, renames)
        replace_user_model_config(
            user_lara_root,
            model_config=native,
            agents=agents,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_model_configuration",
                "path": "model_config",
                "message": str(exc),
            },
        ) from exc


def _validate_native_model_config(
    connections: dict[str, Any],
    models: dict[str, Any],
    groups: dict[str, Any],
) -> dict[str, set[str]]:
    """Validate the native subtree layer by layer.

    Pygent's ``ModelConfig.from_mapping()`` rejects empty ``connections`` and
    ``models``, so it cannot validate the progressively filled states the
    settings UI saves. Each present layer is validated with its own Pygent
    schema and cross-referenced here; the loader runs the whole-mapping parse
    on complete configurations when it loads them.
    """

    parsed_connections: dict[str, ConnectionConfig] = {}
    for name, item in connections.items():
        _require_native_name(name, "connection name")
        if not isinstance(item, dict):
            raise ValueError(f"connection {name!r} must be a mapping")
        parsed_connections[name] = ConnectionConfig.from_mapping(item)
    for key, item in models.items():
        _require_native_name(key, "model key")
        if not isinstance(item, dict):
            raise ValueError(f"model {key!r} must be a mapping")
        entry = EnabledModelConfig.from_mapping(item)
        connection = parsed_connections.get(entry.connection_key)
        if connection is None:
            raise ValueError(
                f"model {key!r} references unknown connection {entry.connection_key!r}"
            )
        if entry.protocol not in connection.protocols:
            raise ValueError(
                f"connection {entry.connection_key!r} does not provide protocol "
                f"{entry.protocol!r}"
            )
    group_models: dict[str, set[str]] = {}
    for name, item in groups.items():
        _require_native_name(name, "model group name")
        if not isinstance(item, dict):
            raise ValueError(f"model group {name!r} must be a mapping")
        group = ModelGroupConfig.from_mapping(item)
        unknown_models = set(group.model_keys) - set(models)
        if unknown_models:
            raise ValueError(
                "model group references an unknown model: "
                + ", ".join(sorted(unknown_models))
            )
        group_models[name] = set(group.model_keys)
    return group_models


def _require_native_name(name: str, label: str) -> None:
    if not name.strip():
        raise ValueError(f"{label} must be a non-empty string")


def _validate_model_key_renames(
    request: UpdateSettingsRequest,
    context: ApiContext,
    models: dict[str, Any],
) -> dict[str, str]:
    """Validate a submitted old-key → new-key rename mapping.

    Renames let the replacement migrate session selections instead of
    rejecting the save; every source must exist in the live configuration,
    every target in the submitted one, and a key cannot be both kept and
    renamed (which also rules out chains through another rename).
    """

    renames = request.model_key_renames
    if not renames:
        return {}
    live = context.config.model_config
    if live is None:
        raise ValueError(
            "model_key_renames requires an existing model configuration"
        )
    new_keys = set(models)
    for previous, current in renames.items():
        _require_native_name(previous, "renamed model key")
        _require_native_name(current, "rename target model key")
        if previous == current:
            raise ValueError(f"model key rename {previous!r} must change the key")
        if previous not in live.models:
            raise ValueError(
                f"model key rename source {previous!r} does not exist in the "
                "current model configuration"
            )
        if current not in new_keys:
            raise ValueError(
                f"model key rename target {current!r} does not exist in the "
                "submitted model configuration"
            )
        if previous in new_keys:
            raise ValueError(
                f"model key {previous!r} cannot be renamed and kept at the "
                "same time"
            )
    return renames


def _session_managers(context: ApiContext) -> list[Any]:
    """Return one session manager per known conversation scope.

    The active scope's manager comes from the context; other scopes get a
    manager built for their own root, deduplicated the same way the
    selection guard deduplicates roots.
    """

    from lara.sessions import SessionManager
    from lara_api.project_state import build_session_scopes

    managers: list[Any] = [context.manager]
    active_root = Path(context.config.lara_root).resolve()
    seen_roots = {active_root}
    for scope in build_session_scopes(
        context.project_state,
        active_workspace_root=context.config.workspace_root,
    ):
        scope_root = Path(scope.lara_root).resolve()
        if scope_root in seen_roots:
            continue
        seen_roots.add(scope_root)
        managers.append(SessionManager(context.config_for_scope(scope)))
    return managers


def _migrate_model_key_renames(context: ApiContext, renames: dict[str, str]) -> None:
    if not renames:
        return
    for previous, current in renames.items():
        for manager in _session_managers(context):
            manager.rename_model_key(previous, current)


def _guard_persisted_session_selections(
    context: ApiContext,
    group_models: dict[str, set[str]],
    renames: dict[str, str] | None = None,
) -> None:
    from lara_api.project_state import build_session_scopes

    roots = [Path(context.config.lara_root) / "sessions"]
    roots.extend(
        Path(scope.lara_root) / "sessions"
        for scope in build_session_scopes(
            context.project_state,
            active_workspace_root=context.config.workspace_root,
        )
    )
    checked_roots: set[Path] = set()
    for root in roots:
        sessions_root = root.resolve()
        if sessions_root in checked_roots or not sessions_root.is_dir():
            continue
        checked_roots.add(sessions_root)
        for metadata_path in sessions_root.glob("*/metadata.json"):
            metadata = read_json(metadata_path)
            group_name = metadata.get("model_group_name")
            model_key = metadata.get("selected_model_key")
            if not isinstance(group_name, str) or not group_name:
                continue
            model_keys = group_models.get(group_name)
            if model_keys is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"Model group {group_name!r} is used by an existing session",
                )
            if isinstance(model_key, str):
                # A rename migrates the stored selection, so the guard judges
                # the key the session will hold after the save, not the stale one.
                effective_key = (renames or {}).get(model_key, model_key)
                if effective_key not in model_keys:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Model {model_key!r} is used by an existing session",
                    )


def _settings_overrides(request: UpdateSettingsRequest) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for request_field, context_field in (
        ("workspace_root", "workspace_root"),
        ("agent_alias", "agent_alias"),
    ):
        if request_field not in request.model_fields_set:
            continue
        overrides[context_field] = non_empty_string(getattr(request, request_field))
    if "max_steps" in request.model_fields_set:
        overrides["max_steps"] = request.max_steps
    if "context_window" in request.model_fields_set:
        overrides["context_window"] = request.context_window
    return overrides
