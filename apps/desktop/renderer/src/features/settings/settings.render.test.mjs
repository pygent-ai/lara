import assert from "node:assert/strict";
import test, { after, before } from "node:test";
import { fileURLToPath } from "node:url";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

const desktopRoot = fileURLToPath(new URL("../../../../", import.meta.url));
let vite;
let page;
let connections;
let groups;
let picker;
let runtime;
let model;

before(async () => {
  vite = await createServer({ appType: "custom", logLevel: "silent", root: desktopRoot, server: { middlewareMode: true } });
  page = await vite.ssrLoadModule("/renderer/src/features/settings/SettingsPage.jsx");
  connections = await vite.ssrLoadModule("/renderer/src/features/settings/ConnectionSettings.jsx");
  groups = await vite.ssrLoadModule("/renderer/src/features/settings/GroupSettings.jsx");
  picker = await vite.ssrLoadModule("/renderer/src/features/settings/ModelPicker.jsx");
  runtime = await vite.ssrLoadModule("/renderer/src/features/settings/RuntimeSettings.jsx");
  model = await vite.ssrLoadModule("/renderer/src/features/settings/settingsModel.js");
});

after(async () => { await vite?.close(); });

const catalogs = {
  providers: { openai: { display_name: "OpenAI", default_protocol: "openai_responses", protocols: { openai_responses: { base_url: "https://api.openai.com/v1", api_key_env: "OPENAI_API_KEY" } } } },
  model_capabilities: [],
};
const capabilities = { modalities: { input: ["text"], output: ["text"] }, streaming: { output: ["text"] }, tools: { call: true, choice: ["auto"], parallel: true }, structured_output: { json_object: true, json_schema: false }, reasoning: { supported: false, controllable: false }, limits: { context_tokens: 128000, max_output_tokens: 8192 } };
const settings = {
  connections: {
    openai: { provider: "openai", credential: { env: "OPENAI_API_KEY" }, credential_source: "user-file:OPENAI_API_KEY", protocols: { openai_responses: { base_url: "https://api.openai.com/v1" } }, verify_ssl: true },
    gateway: { provider: "gateway", credential: { env: "GATEWAY_API_KEY" }, credential_source: "missing", protocols: { openai_chat_completions: { base_url: "https://gw.test/v1" } }, verify_ssl: true },
  },
  models: {
    primary: { connection: "openai", model_id: "gpt-5.1", protocol: "openai_responses", provider_options: {}, capabilities },
    backup: { connection: "gateway", model_id: "glm-5.3", protocol: "openai_chat_completions", provider_options: {}, capabilities },
  },
  model_groups: { coding: { models: ["primary", "backup"] }, cheap: { models: ["backup"] } },
  default_model_group: "coding",
  approvals_enabled: false,
  retry: { max_attempts_per_model: 2, attempt_idle_timeout_seconds: 60 },
};
const noop = () => {};

test("settings page opens on the connections tab with every connection collapsed", () => {
  const html = renderToStaticMarkup(React.createElement(page.SettingsPage, { settings, disabled: false, onClose: noop, onPersist: noop, api: null }));
  assert.match(html, /aria-label="配置分类"/);
  assert.match(html, /aria-label="连接与模型"/);
  assert.match(html, /aria-label="删除连接 openai"/);
  // Saved rows summarise credential state and model count without inputs.
  assert.match(html, /credential-chip ok">已配置/);
  assert.match(html, /credential-chip missing">缺少 API Key/);
  assert.match(html, /1 个模型/);
  const head = html.match(/<div class="connection-row-head">[^]*?<\/div>/)[0];
  assert.doesNotMatch(head, /<input/);
  // The editor is mounted but hidden so collapsing never drops unsaved edits.
  assert.match(html, /<div hidden=""><div class="connection-editor">/);
  // The groups tab stays mounted but hidden; the other sections wait until selected.
  assert.match(html, /<div hidden=""><section class="settings-card group-settings"/);
  assert.doesNotMatch(html, /aria-label="运行与权限"/);
  assert.match(html, /所有修改已保存/);
  assert.doesNotMatch(html, /Save and Reload/);
});

test("connection editor lays out provider, key, endpoints, models and advanced fields", () => {
  const savedDraft = model.settingsToDraft(settings);
  const html = renderToStaticMarkup(React.createElement(connections.ConnectionEditor, {
    previousKey: "openai", initialBuffer: model.connectionBuffer("openai", savedDraft), savedDraft, catalogs, usage: null, api: null, persist: noop, reportDirty: noop,
  }));
  const order = ["aria-label=\"服务商\"", "aria-label=\"连接名称\"", "aria-label=\"API Key\"", "aria-label=\"接口\"", "添加模型", "<details class=\"connection-advanced\"", "aria-label=\"保存连接 openai\""];
  let cursor = -1;
  for (const marker of order) {
    const index = html.indexOf(marker, cursor + 1);
    assert.ok(index > cursor, `${marker} should follow the previous field`);
    cursor = index;
  }
  // A stored key is not re-requested and the row describes the model instead of exposing inputs.
  assert.match(html, /placeholder="留空保留已有密钥"/);
  assert.match(html, /<strong>gpt-5\.1<small class="model-key"> primary<\/small><\/strong><small>工具调用 · 128K 上下文<\/small>/);
  assert.doesNotMatch(html, /aria-label="本地名称"/);
  // Nothing changed yet, so the single save button is disabled.
  assert.ok(html.match(/<button[^>]*aria-label="保存连接 openai"[^>]*>/)[0].includes("disabled"));
});

test("a connection without a stored key explains what blocks its save", () => {
  const savedDraft = model.settingsToDraft(settings);
  const html = renderToStaticMarkup(React.createElement(connections.ConnectionEditor, {
    previousKey: "gateway", initialBuffer: model.connectionBuffer("gateway", savedDraft), savedDraft, catalogs, usage: null, api: null, persist: noop, reportDirty: noop,
  }));
  assert.match(html, /credential-chip missing/);
  assert.match(html, /placeholder="粘贴 API Key"/);
  assert.match(html, /class="editor-hint">请填写 API Key。/);
  // A custom provider shows up as 自定义 and its endpoint offers every protocol.
  assert.match(html, /<option value="__custom__" selected="">/);
  assert.match(html, /Gemini Generate Content/);
});

test("a new connection starts at the provider choice and offers nothing else yet", () => {
  const savedDraft = model.settingsToDraft(settings);
  const html = renderToStaticMarkup(React.createElement(connections.ConnectionEditor, {
    previousKey: null, initialBuffer: model.newConnectionBuffer("", savedDraft, catalogs), savedDraft, catalogs, usage: null, api: null, persist: noop, reportDirty: noop, onCreated: noop, onCancelCreate: noop,
  }));
  assert.match(html, /<option value="" disabled="" selected="">请选择服务商/);
  assert.doesNotMatch(html, /aria-label="API Key"/);
  assert.doesNotMatch(html, /添加模型/);
  assert.ok(html.match(/<button[^>]*aria-label="保存新连接"[^>]*>/)[0].includes("disabled"));
});

test("model picker lists discovered models with already-added ones locked", () => {
  const connection = { ...settings.connections.openai };
  const html = renderToStaticMarkup(React.createElement(picker.ModelPicker, { connection, credentialValue: "", existingIds: ["gpt-5.1"], api: null, onAdd: noop, onCancel: noop }));
  assert.match(html, /role="dialog" aria-label="添加模型"/);
  assert.match(html, /aria-multiselectable="true"/);
  assert.match(html, /placeholder="搜索模型，或输入服务商未列出的模型 ID"/);
  // One endpoint means no protocol choice; the submit waits for a selection.
  assert.doesNotMatch(html, /aria-label="API 协议"/);
  assert.match(html, /<button[^>]*class="primary-action" disabled="">添加模型/);
  assert.deepEqual(picker.filterDiscoveredModels([{ id: "gpt-5.1" }, { id: "o4-mini" }], "GPT"), [{ id: "gpt-5.1" }]);
  const multi = renderToStaticMarkup(React.createElement(picker.ModelPicker, { connection: { ...settings.connections.openai, protocols: { openai_responses: { base_url: "x" }, openai_chat_completions: { base_url: "y" } } }, credentialValue: "", existingIds: [], api: null, onAdd: noop, onCancel: noop }));
  assert.match(multi, /aria-label="API 协议"/);
});

test("group settings render the list beside the selected group's editor", () => {
  const savedDraft = model.settingsToDraft(settings);
  const html = renderToStaticMarkup(React.createElement(groups.GroupSettings, { savedDraft, usage: { models: {}, groups: { cheap: 2 } }, persist: noop, reportDirty: noop }));
  assert.match(html, /aria-label="模型组列表"/);
  assert.match(html, /<strong>coding<span class="default-chip">默认<\/span><\/strong>/);
  assert.match(html, /aria-label="设为默认模型组 cheap"/);
  assert.doesNotMatch(html, /aria-label="设为默认模型组 coding"/);
  assert.match(html, /aria-label="删除模型组 coding"/);
  // The editor opens on the first group: primary select plus one ordered fallback.
  assert.match(html, /aria-label="编辑模型组 coding"/);
  assert.match(html, /<option value="primary" selected="">gpt-5\.1 \(primary\)<\/option>/);
  assert.match(html, /<span class="fallback-rank">1<\/span><span class="fallback-copy"><strong>glm-5\.3<\/strong>/);
  assert.ok(html.match(/<button[^>]*aria-label="保存模型组 coding"[^>]*>/)[0].includes("disabled"));
  assert.match(html, /新建模型组/);
  // Without models the tab points back to the connections tab.
  const empty = renderToStaticMarkup(React.createElement(groups.GroupSettings, { savedDraft: model.settingsToDraft({}), usage: null, persist: noop, reportDirty: noop }));
  assert.match(empty, /先在「连接与模型」中添加并保存模型/);
});

test("runtime section carries the tool permission mode and the retry policy", () => {
  for (const enabled of [true, false]) {
    const fields = model.settingsToDraft({ ...settings, approvals_enabled: enabled });
    const html = renderToStaticMarkup(React.createElement(runtime.RuntimeSettings, { fields, setField: noop, dirty: false, onSave: noop }));
    assert.match(html, /工具权限/);
    assert.match(html, new RegExp(`value="${enabled ? "approval" : "full-access"}" selected=""`));
    assert.match(html, /模型重试与切换/);
    assert.match(html, /每个模型尝试次数/);
    assert.ok(html.match(/<button[^>]*aria-label="保存运行与权限"[^>]*>/)[0].includes("disabled"));
  }
});
