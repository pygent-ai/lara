from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest
from pygent.runtime import SQLiteModelDeploymentStore

from lora.runtime.service import LoraRuntimeService
from tests.unit.test_model_configuration import native_runtime_config


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["profile", "admission"])
async def test_old_model_json_does_not_block_new_runtime(tmp_path: Path, location: str) -> None:
    config = native_runtime_config(tmp_path)
    config.runtime_durability.history_path = str(tmp_path / "executions.sqlite3")
    legacy = tmp_path / "model-deployments-v1.sqlite3"
    store = SQLiteModelDeploymentStore(legacy)
    await store.open()
    await store.close()
    snapshot = {
        "model_group": {
            "name": "lora:dev", "routes": [], "fallback": [],
            "capacity_key": None, "max_concurrency": None, "resolution": "concrete",
        },
    }
    with sqlite3.connect(legacy) as db:
        if location == "profile":
            db.execute("INSERT INTO pygent_model_profiles VALUES(?,?,?,?,0)",
                       ("scope", "lora:dev", "default", json.dumps(snapshot)))
        else:
            admission = {"snapshots": [{"group_name": "lora:dev", "snapshot": snapshot}]}
            db.execute("INSERT INTO pygent_model_admissions VALUES(?,?,?,1)",
                       ("old-execution", "scope", json.dumps(admission)))

    # Reproduce the reported failure using the official loader and legacy data.
    broken = SQLiteModelDeploymentStore(legacy)
    try:
        with pytest.raises(ValueError, match="stored model group fields"):
            await broken.open()
    finally:
        await broken.close()
    original = legacy.read_bytes()

    service = LoraRuntimeService(config)
    try:
        agent = service.new_agent(interactive_approvals=False)
        await service.bind(agent, agent)
        assert Path(service.model_store.path).name.startswith("model-deployments-v2-")
    finally:
        await service.close()
    assert legacy.read_bytes() == original


@pytest.mark.asyncio
async def test_current_store_keeps_its_namespace_on_restart(tmp_path: Path) -> None:
    config = native_runtime_config(tmp_path)
    config.runtime_durability.history_path = str(tmp_path / "executions.sqlite3")
    legacy = tmp_path / "model-deployments-v1.sqlite3"
    store = SQLiteModelDeploymentStore(legacy)
    await store.open()
    await store.close()
    namespaces = []
    for _ in range(2):
        service = LoraRuntimeService(config)
        try:
            agent = service.new_agent(interactive_approvals=False)
            await service.bind(agent, agent)
            assert Path(service.model_store.path).parent == tmp_path
            assert Path(service.model_store.path).name.startswith("model-deployments-v2-")
            namespaces.append(service.model_store.namespace_id)
        finally:
            await service.close()
    assert namespaces[0] == namespaces[1]
    assert len(list(tmp_path.glob("model-deployments-v2-*.sqlite3"))) == 1


@pytest.mark.asyncio
async def test_model_config_generation_uses_separate_store_while_old_runtime_is_live(
    tmp_path: Path,
) -> None:
    old_config = native_runtime_config(tmp_path)
    old_config.runtime_durability.history_path = str(tmp_path / "executions.sqlite3")
    new_config = native_runtime_config(tmp_path)
    new_config.runtime_durability.history_path = str(tmp_path / "executions.sqlite3")
    mapping = deepcopy(new_config.model_config_mapping)
    mapping["models"]["main"]["model_id"] = "model-main-updated"
    new_config.model_config_mapping = mapping
    new_config.model_config = None
    new_config.__post_init__()

    old_service = LoraRuntimeService(old_config)
    new_service = LoraRuntimeService(new_config)
    try:
        old_agent = old_service.new_agent(interactive_approvals=False)
        await old_service.bind(old_agent, old_agent)
        new_agent = new_service.new_agent(interactive_approvals=False)
        await new_service.bind(new_agent, new_agent)

        assert Path(old_service.model_store.path) != Path(new_service.model_store.path)
        assert Path(old_service.model_store.path).exists()
        assert Path(new_service.model_store.path).exists()
    finally:
        await new_service.close()
        await old_service.close()
