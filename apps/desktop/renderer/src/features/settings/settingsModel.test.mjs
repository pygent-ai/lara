import test from "node:test";
import assert from "node:assert/strict";
import {
  applyBuiltinProvider,
  commitConnectionBuffer,
  commitConnectionFields,
  commitModelDelete,
  commitModelSave,
  connectionFieldsDirty,
  connectionSliceDirty,
  emptyNativeConnection,
  emptyNativeModel,
  modelDiscoveryRequest,
  modelKeyRenamesForSave,
  modelRowDirty,
  settingsToDraft,
  modelGroupValidationError,
} from "./settingsModel.js";
import { settingsPayload } from "../../shared/api/client.js";

test("model key renames reach the backend only while the saved source exists", () => {
  const savedDraft = settingsToDraft({
    connections: { service: emptyNativeConnection() },
    models: { "model-1": { connection: "service", model_id: "m", protocol: "openai_responses", capabilities: { limits: {} } } },
    model_groups: { coding: { models: ["model-1"] } },
    default_model_group: "coding",
  });
  const buffer = {
    key: "service",
    connection: savedDraft.connections.service,
    models: { renamed: savedDraft.models["model-1"] },
    renames: { "model-1": "renamed" },
    credentialValues: {},
  };

  assert.deepEqual(modelKeyRenamesForSave(savedDraft, buffer, "renamed"), { "model-1": "renamed" });
  assert.equal(modelKeyRenamesForSave(savedDraft, buffer, "model-1"), undefined);
  // Once the rename is committed the saved source is gone, so later saves of
  // the same editor never re-send the stale rename.
  const committed = { ...savedDraft, models: { renamed: savedDraft.models["model-1"] } };
  assert.equal(modelKeyRenamesForSave(committed, buffer, "renamed"), undefined);
});

test("connection commit adds a model without group membership and stays valid", () => {
  const draft = settingsToDraft({
    connections: { service: emptyNativeConnection() },
    models: { existing: { connection: "service", model_id: "existing", protocol: "openai_responses", capabilities: { limits: {} } } },
    model_groups: { coding: { models: ["existing"] } },
    default_model_group: "coding",
  });
  const buffer = {
    key: "service",
    connection: draft.connections.service,
    models: { ...draft.models, "model-2": { ...emptyNativeModel("service", "openai_responses"), model_id: "new-model" } },
    renames: {},
    credentialValues: {},
  };
  const next = commitConnectionBuffer(draft, "service", buffer, null);
  assert.equal(modelGroupValidationError(next), "");
  assert.equal(next.models["model-2"].connection, "service");
  assert.equal(next.models["model-2"].model_id, "new-model");
  // Models are only selected into groups from the 模型组 tab, so a new model
  // starts outside every group and existing priorities stay untouched.
  assert.deepEqual(next.modelGroups.coding.models, ["existing"]);
  assert.equal(Object.hasOwn(draft.models, "model-2"), false);
  const saved = settingsPayload(next).model_config;
  assert.equal(saved.models["model-2"].connection, "service");
  assert.equal(Object.hasOwn(saved.models["model-2"], "credential"), false);
});

test("built-in provider applies Pygent endpoints and carries the entered key", () => {
  const draft = settingsToDraft({
    connections: { custom: { provider: "custom", credential: { env: "CUSTOM_KEY" }, protocols: { openai_chat_completions: { base_url: "https://custom.test/v1" } } } },
    models: { main: { connection: "custom", protocol: "openai_chat_completions", model_id: "model" } },
  });
  draft.credentialValues.CUSTOM_KEY = "temporary-secret";
  const catalog = {
    default_protocol: "anthropic_messages",
    protocols: { anthropic_messages: { base_url: "https://api.anthropic.com", api_key_env: "ANTHROPIC_API_KEY" } },
  };
  const next = applyBuiltinProvider(draft, "custom", "anthropic", catalog);
  assert.deepEqual(next.connections.custom.protocols, { anthropic_messages: { base_url: "https://api.anthropic.com" } });
  assert.equal(next.connections.custom.provider, "anthropic");
  assert.deepEqual(next.connections.custom.credential, { env: "ANTHROPIC_API_KEY" });
  assert.equal(next.credentialValues.ANTHROPIC_API_KEY, "temporary-secret");
  assert.equal(next.models.main.protocol, "anthropic_messages");
  assert.equal(draft.models.main.protocol, "openai_chat_completions");
});

test("switching connection protocol keeps addresses, credentials and model references", async () => {
  const { changeConnectionProtocol } = await import("./settingsModel.js");
  const draft = settingsToDraft({ connections: { service: emptyNativeConnection() }, models: { main: { connection: "service", protocol: "openai_responses", model_id: "example" }, other: { connection: "other", protocol: "openai_responses" } } });
  const next = changeConnectionProtocol(draft, "service", "openai_responses", "anthropic_messages");
  assert.equal(next.connections.service.protocols.anthropic_messages.base_url, draft.connections.service.protocols.openai_responses.base_url);
  assert.equal(next.connections.service.credential, draft.connections.service.credential);
  assert.equal(next.models.main.protocol, "anthropic_messages");
  assert.equal(next.models.other, draft.models.other);
  assert.equal(next.modelGroups, draft.modelGroups);
  assert.equal(changeConnectionProtocol(draft, "service", "openai_responses", "openai_chat_completions"), draft);
  assert.equal(draft.models.main.protocol, "openai_responses");
});

test("model discovery uses the selected connection and unsaved credential", () => {
  const draft = settingsToDraft({ connections: { service: { ...emptyNativeConnection(), credential_source: "missing" } } });
  draft.credentialValues.OPENAI_API_KEY = "temporary-secret";
  const request = modelDiscoveryRequest(draft, "service");
  assert.equal(request.protocol, "openai_responses");
  assert.equal(request.credential_value, "temporary-secret");
  assert.equal(request.connection.credential.env, "OPENAI_API_KEY");
  assert.equal(Object.hasOwn(request.connection, "credential_source"), false);
  assert.equal(modelDiscoveryRequest(draft, "missing"), null);
});

test("connection and model dirtiness report their own save units", () => {
  const draft = settingsToDraft({
    connections: { service: emptyNativeConnection() },
    models: {
      main: { connection: "service", model_id: "main", protocol: "openai_responses", capabilities: { limits: {} } },
      other: { connection: "service", model_id: "other", protocol: "openai_responses", capabilities: { limits: {} } },
    },
    model_groups: { coding: { models: ["main", "other"] } },
    default_model_group: "coding",
  });
  const buffer = { key: "service", connection: draft.connections.service, models: structuredClone(draft.models), renames: {}, credentialValues: {} };
  assert.equal(connectionFieldsDirty(draft, "service", buffer), false);
  assert.equal(modelRowDirty(draft, buffer, "main"), false);
  assert.equal(connectionSliceDirty(draft, "service", buffer), false);

  // A model edit leaves the connection fields clean and only that row dirty.
  buffer.models.main = { ...buffer.models.main, model_id: "renamed-id" };
  assert.equal(connectionFieldsDirty(draft, "service", buffer), false);
  assert.equal(modelRowDirty(draft, buffer, "main"), true);
  assert.equal(modelRowDirty(draft, buffer, "other"), false);
  assert.equal(connectionSliceDirty(draft, "service", buffer), true);

  // A pending rename marks the row dirty against its saved key.
  buffer.models.gpu = buffer.models.main;
  delete buffer.models.main;
  buffer.renames = { main: "gpu" };
  assert.equal(modelRowDirty(draft, buffer, "gpu"), true);

  // A connection field change marks the head save without touching rows.
  buffer.connection = { ...buffer.connection, proxy: "http://127.0.0.1:7890" };
  assert.equal(connectionFieldsDirty(draft, "service", buffer), true);
});

test("connection field save carries protocols but not in-progress model edits", () => {
  const draft = settingsToDraft({
    connections: { service: emptyNativeConnection() },
    models: {
      main: { connection: "service", model_id: "main", protocol: "openai_responses", capabilities: { limits: {} } },
      other: { connection: "service", model_id: "other", protocol: "openai_responses", capabilities: { limits: {} } },
    },
    model_groups: { coding: { models: ["main", "other"] } },
    default_model_group: "coding",
  });
  const buffer = { key: "service", connection: draft.connections.service, models: structuredClone(draft.models), renames: {}, credentialValues: {} };
  // One model has an unsaved capability edit; the other rides a protocol change.
  buffer.models.other = { ...buffer.models.other, capabilities: { limits: { context_tokens: 999 } } };
  buffer.models.main = { ...buffer.models.main, protocol: "openai_chat_completions" };
  buffer.connection = { ...buffer.connection, protocols: { ...buffer.connection.protocols, openai_chat_completions: { base_url: "https://example.test/v1" } } };

  const next = commitConnectionFields(draft, "service", buffer, null);
  assert.equal(next.connections.service.proxy, undefined);
  assert.equal(next.models.main.protocol, "openai_chat_completions");
  // The protocol rides the connection save; the capability edit stays local.
  assert.deepEqual(next.models.other.capabilities, draft.models.other.capabilities);
  assert.equal(modelRowDirty(next, buffer, "main"), false);
  assert.equal(modelRowDirty(next, buffer, "other"), true);
});

test("model row save commits its entry and remaps a pending rename in groups", () => {
  const draft = settingsToDraft({
    connections: { service: emptyNativeConnection() },
    models: {
      main: { connection: "service", model_id: "main", protocol: "openai_responses", capabilities: { limits: {} } },
      spare: { connection: "service", model_id: "spare", protocol: "openai_responses", capabilities: { limits: {} } },
    },
    model_groups: { coding: { models: ["main", "spare"] } },
    default_model_group: "coding",
  });
  const buffer = { key: "service", connection: draft.connections.service, models: structuredClone(draft.models), renames: {}, credentialValues: {} };

  buffer.models.main = { ...buffer.models.main, model_id: "main-v2" };
  const updated = commitModelSave(draft, "service", buffer, null, "main");
  assert.equal(updated.models.main.model_id, "main-v2");
  assert.equal(modelGroupValidationError(updated), "");

  // Renaming main to gpu moves the group member in the same commit.
  buffer.models.gpu = buffer.models.main;
  delete buffer.models.main;
  buffer.renames = { main: "gpu" };
  const renamed = commitModelSave(draft, "service", buffer, null, "gpu");
  assert.equal(renamed.models.gpu.model_id, "main-v2");
  assert.equal(Object.hasOwn(renamed.models, "main"), false);
  assert.deepEqual(renamed.modelGroups.coding.models, ["gpu", "spare"]);
  assert.equal(modelGroupValidationError(renamed), "");
});

test("model deletion scrubs group membership and drops emptied groups", () => {
  const draft = settingsToDraft({
    connections: { service: emptyNativeConnection() },
    models: {
      main: { connection: "service", model_id: "main", protocol: "openai_responses", capabilities: { limits: {} } },
      spare: { connection: "service", model_id: "spare", protocol: "openai_responses", capabilities: { limits: {} } },
    },
    model_groups: { coding: { models: ["main", "spare"] }, extras: { models: ["main"] } },
    default_model_group: "coding",
  });
  const buffer = { key: "service", connection: draft.connections.service, models: structuredClone(draft.models), renames: {}, credentialValues: {} };

  const next = commitModelDelete(draft, "service", buffer, null, "main");
  assert.equal(Object.hasOwn(next.models, "main"), false);
  assert.equal(Object.hasOwn(next.models, "spare"), true);
  // `extras` only held the deleted model, so it disappears with it.
  assert.deepEqual(next.modelGroups.coding.models, ["spare"]);
  assert.equal(Object.hasOwn(next.modelGroups, "extras"), false);
  assert.equal(modelGroupValidationError({ ...next, defaultModelGroup: "coding" }), "");
});
