import test from "node:test";
import assert from "node:assert/strict";
import { applyBuiltinProvider, commitConnectionBuffer, emptyNativeConnection, emptyNativeModel, modelDiscoveryRequest, settingsToDraft, modelGroupValidationError } from "./settingsModel.js";
import { settingsPayload } from "../../shared/api/client.js";

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
