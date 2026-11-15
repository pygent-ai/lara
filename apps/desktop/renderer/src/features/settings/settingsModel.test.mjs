import test from "node:test";
import assert from "node:assert/strict";
import {
  capabilitySummary,
  commitConnection,
  commitConnectionDelete,
  commitGroup,
  commitGroupDelete,
  commitModelDelete,
  connectionBuffer,
  connectionDirty,
  connectionError,
  connectionForProvider,
  deletionImpact,
  groupDirty,
  groupError,
  modelDiscoveryRequest,
  newConnectionBuffer,
  newModel,
  protocolChoices,
  removeBufferModel,
  renameBufferModel,
  settingsForSave,
  settingsToDraft,
  switchBufferProvider,
  uniqueKey,
} from "./settingsModel.js";

const catalogs = {
  providers: {
    openai: { display_name: "OpenAI", default_protocol: "openai_responses", protocols: { openai_responses: { base_url: "https://api.openai.com/v1", api_key_env: "OPENAI_API_KEY" }, openai_chat_completions: { base_url: "https://api.openai.com/v1", api_key_env: "OPENAI_API_KEY" } } },
    anthropic: { display_name: "Anthropic", default_protocol: "anthropic_messages", protocols: { anthropic_messages: { base_url: "https://api.anthropic.com", api_key_env: "ANTHROPIC_API_KEY" } } },
  },
  model_capabilities: [
    { provider: "openai", protocol: "openai_responses", model_id: "gpt-known", capabilities: { modalities: { input: ["text", "image"], output: ["text"] }, streaming: { output: ["text"] }, tools: { call: true, choice: ["auto"], parallel: true }, structured_output: { json_object: true, json_schema: true }, reasoning: { supported: true, controllable: true }, limits: { context_tokens: 400000, max_output_tokens: 32000 } } },
  ],
};

const capabilities = { modalities: { input: ["text"], output: ["text"] }, streaming: { output: ["text"] }, tools: { call: true, choice: ["auto"], parallel: true }, structured_output: { json_object: true, json_schema: false }, reasoning: { supported: false, controllable: false }, limits: { context_tokens: 128000, max_output_tokens: 8192 } };

function saved() {
  return settingsToDraft({
    connections: {
      openai: { provider: "openai", credential: { env: "OPENAI_API_KEY" }, credential_source: "user-file:OPENAI_API_KEY", protocols: { openai_responses: { base_url: "https://api.openai.com/v1" } }, verify_ssl: true },
      zhipu: { provider: "zhipu", credential: { env: "ZHIPU_API_KEY" }, credential_source: "missing", protocols: { openai_chat_completions: { base_url: "https://open.bigmodel.cn/api/paas/v4" } }, verify_ssl: true },
    },
    models: {
      main: { connection: "openai", model_id: "gpt-5.1", protocol: "openai_responses", provider_options: {}, capabilities },
      spare: { connection: "openai", model_id: "gpt-5.4", protocol: "openai_responses", provider_options: {}, capabilities },
      glm: { connection: "zhipu", model_id: "glm-5.3", protocol: "openai_chat_completions", provider_options: {}, capabilities },
    },
    model_groups: { coding: { models: ["main", "spare"] }, cheap: { models: ["glm"] } },
    default_model_group: "coding",
    retry: { max_attempts_per_model: 3 },
  });
}

test("settingsToDraft normalises defaults and never carries secrets", () => {
  const draft = settingsToDraft({});
  assert.deepEqual(draft.connections, {});
  assert.equal(draft.defaultModelGroup, "");
  assert.equal(draft.retry.max_attempts_per_model, 2);
  assert.deepEqual(draft.credentialValues, {});
  assert.equal(saved().retry.max_attempts_per_model, 3);
});

test("settingsForSave resets an untouched agent when the workspace changes", () => {
  const draft = { workspaceRoot: "C:/new", agent: "default" };
  assert.equal(settingsForSave(draft, { workspace_root: "C:/old", agent: "default" }).agent, "");
  assert.equal(settingsForSave({ ...draft, agent: "reviewer" }, { workspace_root: "C:/old", agent: "default" }).agent, "reviewer");
  assert.equal(settingsForSave(draft, { workspace_root: "C:/new", agent: "default" }).agent, "default");
});

test("uniqueKey appends a counter only on collisions", () => {
  assert.equal(uniqueKey("gpt-5", {}), "gpt-5");
  assert.equal(uniqueKey("gpt-5", { "gpt-5": 1 }), "gpt-5-2");
  assert.equal(uniqueKey("gpt-5", { "gpt-5": 1, "gpt-5-2": 1 }), "gpt-5-3");
  assert.equal(uniqueKey("  ", {}), "item");
});

test("connectionForProvider takes catalog defaults and leaves custom providers open", () => {
  const openai = connectionForProvider("openai", "openai", catalogs);
  assert.deepEqual(Object.keys(openai.protocols), ["openai_responses", "openai_chat_completions"]);
  assert.deepEqual(openai.credential, { env: "OPENAI_API_KEY" });
  assert.equal(openai.credential_source, "missing");
  const custom = connectionForProvider("my-gateway", "my-gateway", catalogs);
  assert.equal(custom.provider, "my-gateway");
  assert.deepEqual(custom.credential, { env: "MY_GATEWAY_API_KEY" });
  assert.deepEqual(custom.protocols, { openai_chat_completions: { base_url: "" } });
  assert.equal(connectionForProvider("", "x", catalogs).provider, "");
  assert.deepEqual(protocolChoices("anthropic", catalogs), ["anthropic_messages"]);
  assert.deepEqual(protocolChoices("my-gateway", catalogs), ["openai_responses", "openai_chat_completions", "anthropic_messages", "gemini_generate_content"]);
});

test("newModel prefers catalog capabilities and falls back to the agentic default", () => {
  const connection = connectionForProvider("openai", "openai", catalogs);
  const known = newModel("openai", connection, "gpt-known", "openai_responses", catalogs);
  assert.equal(known.capabilities.limits.context_tokens, 400000);
  const unknown = newModel("openai", connection, "gpt-new", "openai_responses", catalogs);
  assert.equal(unknown.capabilities.limits.context_tokens, 128000);
  assert.equal(unknown.capabilities.tools.call, true);
  assert.equal(capabilitySummary(known.capabilities), "工具调用 · 推理 · 输入图片 · 400K 上下文");
  assert.equal(capabilitySummary(null), "未声明能力");
});

test("modelDiscoveryRequest strips the UI-only credential source", () => {
  const connection = { ...connectionForProvider("openai", "openai", catalogs), credential_source: "missing" };
  const request = modelDiscoveryRequest(connection, "openai_responses", "sk-typed");
  assert.equal(request.protocol, "openai_responses");
  assert.equal(request.credential_value, "sk-typed");
  assert.equal(Object.hasOwn(request.connection, "credential_source"), false);
  assert.equal(modelDiscoveryRequest(connection, "", ""), null);
});

test("connection validation covers name, provider, endpoints, credential and models", () => {
  const base = saved();
  const buffer = connectionBuffer("openai", base);
  assert.equal(connectionError(buffer, base, "openai"), "");
  assert.match(connectionError({ ...buffer, key: " " }, base, "openai"), /连接名称/);
  assert.match(connectionError({ ...buffer, key: "zhipu" }, base, "openai"), /已存在/);
  assert.match(connectionError({ ...buffer, connection: { ...buffer.connection, provider: "" } }, base, "openai"), /服务商/);
  assert.match(connectionError({ ...buffer, connection: { ...buffer.connection, protocols: {} } }, base, "openai"), /接口/);
  assert.match(connectionError({ ...buffer, connection: { ...buffer.connection, protocols: { openai_responses: { base_url: " " } } } }, base, "openai"), /Base URL/);
  assert.match(connectionError({ ...buffer, models: { ...buffer.models, main: { ...buffer.models.main, model_id: "" } } }, base, "openai"), /模型 main 缺少模型 ID/);
  // A connection without a stored key needs one typed before it can be saved.
  const zhipu = connectionBuffer("zhipu", base);
  assert.match(connectionError(zhipu, base, "zhipu"), /API Key/);
  assert.equal(connectionError({ ...zhipu, credentialValue: "sk-1" }, base, "zhipu"), "");
  assert.equal(connectionError({ ...zhipu, connection: { ...zhipu.connection, credential: { none: true } } }, base, "zhipu"), "");
});

test("connection dirtiness ignores renames bookkeeping but counts a typed key", () => {
  const base = saved();
  const buffer = connectionBuffer("openai", base);
  assert.equal(connectionDirty(buffer, base, "openai"), false);
  assert.equal(connectionDirty({ ...buffer, credentialValue: "sk-new" }, base, "openai"), true);
  assert.equal(connectionDirty({ ...buffer, connection: { ...buffer.connection, proxy: "http://127.0.0.1:7890" } }, base, "openai"), true);
  assert.equal(connectionDirty(newConnectionBuffer("openai", base, catalogs), base, null), true);
});

test("renaming and removing buffer models keeps the saved-key mapping", () => {
  const base = saved();
  let buffer = connectionBuffer("openai", base);
  buffer = renameBufferModel(buffer, "main", "primary");
  assert.deepEqual(buffer.renames, { main: "primary" });
  buffer = renameBufferModel(buffer, "primary", "gpu");
  assert.deepEqual(buffer.renames, { main: "gpu" });
  assert.deepEqual(Object.keys(buffer.models), ["gpu", "spare"]);
  // Renaming back to the saved key clears the entry; collisions are ignored.
  assert.deepEqual(renameBufferModel(buffer, "gpu", "main").renames, {});
  assert.equal(renameBufferModel(buffer, "gpu", "spare"), buffer);
  const removed = removeBufferModel(buffer, "gpu");
  assert.deepEqual(Object.keys(removed.models), ["spare"]);
  assert.deepEqual(removed.renames, {});
});

test("commitConnection replaces the slice, remaps renamed group members and stages the key", () => {
  const base = saved();
  let buffer = connectionBuffer("openai", base);
  buffer = renameBufferModel(buffer, "main", "primary");
  buffer.models.extra = newModel("openai", buffer.connection, "gpt-extra", "openai_responses", catalogs);
  buffer = { ...buffer, key: "openai-main", credentialValue: " sk-rotated " };

  const next = commitConnection(base, "openai", buffer);
  assert.deepEqual(Object.keys(next.connections).sort(), ["openai-main", "zhipu"]);
  assert.deepEqual(Object.keys(next.models).sort(), ["extra", "glm", "primary", "spare"]);
  assert.equal(next.models.primary.connection, "openai-main");
  assert.equal(next.models.glm.connection, "zhipu");
  assert.deepEqual(next.modelGroups.coding.models, ["primary", "spare"]);
  assert.deepEqual(next.credentialValues, { OPENAI_API_KEY: "sk-rotated" });
  assert.equal(next.defaultModelGroup, "coding");
  // The saved draft is never mutated.
  assert.deepEqual(Object.keys(base.models).sort(), ["glm", "main", "spare"]);
});

test("switching a buffer to another provider re-targets models that lose their protocol", () => {
  const base = saved();
  const buffer = switchBufferProvider(connectionBuffer("openai", base), "anthropic", catalogs);
  assert.deepEqual(Object.keys(buffer.connection.protocols), ["anthropic_messages"]);
  assert.equal(buffer.models.main.protocol, "anthropic_messages");
  assert.equal(buffer.connection.credential_source, "missing");
  assert.deepEqual(buffer.connection.credential, { env: "ANTHROPIC_API_KEY" });
});

test("deleting a model or connection scrubs groups and repoints the default", () => {
  const base = saved();
  const withoutGlm = commitModelDelete(base, "glm");
  assert.equal(Object.hasOwn(withoutGlm.models, "glm"), false);
  assert.equal(Object.hasOwn(withoutGlm.modelGroups, "cheap"), false);
  assert.equal(withoutGlm.defaultModelGroup, "coding");

  const withoutOpenai = commitConnectionDelete(base, "openai");
  assert.deepEqual(Object.keys(withoutOpenai.connections), ["zhipu"]);
  assert.deepEqual(Object.keys(withoutOpenai.models), ["glm"]);
  assert.deepEqual(Object.keys(withoutOpenai.modelGroups), ["cheap"]);
  assert.equal(withoutOpenai.defaultModelGroup, "cheap");

  const empty = commitConnectionDelete(withoutOpenai, "zhipu");
  assert.deepEqual(empty.modelGroups, {});
  assert.equal(empty.defaultModelGroup, "");
});

test("group validation and commit cover naming, membership and the default pointer", () => {
  const base = saved();
  assert.equal(groupError({ name: "coding", models: ["main"] }, base, "coding"), "");
  assert.match(groupError({ name: "", models: ["main"] }, base, null), /名称/);
  assert.match(groupError({ name: "cheap", models: ["main"] }, base, "coding"), /已存在/);
  assert.match(groupError({ name: "x", models: [] }, base, null), /主模型/);
  assert.match(groupError({ name: "x", models: ["ghost"] }, base, null), /已不存在/);
  assert.match(groupError({ name: "x", models: ["main", "main"] }, base, null), /只能出现一次/);
  assert.equal(groupDirty({ name: "coding", models: ["main", "spare"] }, base, "coding"), false);
  assert.equal(groupDirty({ name: "coding", models: ["spare", "main"] }, base, "coding"), true);

  const renamed = commitGroup(base, "coding", { name: "dev", models: ["spare", "main"] });
  assert.deepEqual(Object.keys(renamed.modelGroups), ["cheap", "dev"]);
  assert.equal(renamed.defaultModelGroup, "dev");
  const created = commitGroup({ ...base, modelGroups: {}, defaultModelGroup: "" }, null, { name: "first", models: ["main"] });
  assert.equal(created.defaultModelGroup, "first");

  const deleted = commitGroupDelete(base, "coding");
  assert.deepEqual(Object.keys(deleted.modelGroups), ["cheap"]);
  assert.equal(deleted.defaultModelGroup, "cheap");
});

test("deletionImpact explains how affected chats are rebound", () => {
  const base = saved();
  const usage = { models: { main: 2, glm: 1 }, groups: { coding: 2, cheap: 1 } };
  assert.equal(deletionImpact("model", "spare", base, usage), "");
  assert.match(deletionImpact("model", "main", base, usage), /2 个对话.*下一个模型/);
  assert.match(deletionImpact("model", "glm", base, usage), /模型组「cheap」会随之删除/);
  assert.match(deletionImpact("connection", "openai", base, usage), /2 个对话.*此连接下的模型/);
  assert.match(deletionImpact("group", "cheap", base, usage), /改用「coding」/);
  assert.match(deletionImpact("group", "coding", base, usage), /改用「cheap」/);
  assert.match(deletionImpact("group", "coding", { ...base, modelGroups: { coding: base.modelGroups.coding } }, usage), /重新选择/);
  assert.equal(deletionImpact("group", "coding", base, null), "");
});
