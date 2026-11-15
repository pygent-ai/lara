// Pure settings model: draft conversion, factories, validation and the commit
// functions that merge one editor's result over the last saved draft. Every
// editor (connection, model group) keeps its own buffer and commits through
// here, so saving one entity never carries another entity's pending edits.

export const PROTOCOL_LABELS = {
  openai_chat_completions: "OpenAI Chat Completions",
  openai_responses: "OpenAI Responses",
  anthropic_messages: "Anthropic Messages",
  gemini_generate_content: "Gemini Generate Content",
};

export function protocolLabel(protocol) {
  return PROTOCOL_LABELS[protocol] || protocol || "未选择协议";
}

export function settingsToDraft(settings) {
  return {
    approvalsEnabled: settings.approvals_enabled !== false,
    workspaceRoot: settings.workspace_root || "",
    agent: settings.agent || "",
    connections: structuredClone(settings.connections || {}),
    models: structuredClone(settings.models || {}),
    modelGroups: structuredClone(settings.model_groups || {}),
    defaultModelGroup: settings.default_model_group || "",
    credentialValues: {},
    retry: {
      max_attempts_per_model: settings.retry?.max_attempts_per_model ?? 2,
      attempt_idle_timeout_seconds: settings.retry?.attempt_idle_timeout_seconds ?? 60,
      backoff_initial: settings.retry?.backoff_initial ?? 0.5,
      backoff_maximum: settings.retry?.backoff_maximum ?? 4,
      backoff_multiplier: settings.retry?.backoff_multiplier ?? 2,
    },
    maxSteps: Number.isFinite(settings.max_steps) ? settings.max_steps : -1,
    contextWindow: Number.isFinite(settings.context_window) ? String(settings.context_window) : "",
  };
}

// A changed workspace resets an agent the user left untouched, so the new
// project starts from its own default agent instead of the previous one.
export function settingsForSave(draft, settings) {
  const workspaceChanged = draft.workspaceRoot.trim() !== (settings.workspace_root || "").trim();
  const keptPreviousAgent = draft.agent.trim() === (settings.agent || "").trim();
  return workspaceChanged && keptPreviousAgent ? { ...draft, agent: "" } : draft;
}

export function uniqueKey(base, taken) {
  const root = base.trim() || "item";
  if (!Object.hasOwn(taken, root)) return root;
  let index = 2;
  while (Object.hasOwn(taken, `${root}-${index}`)) index += 1;
  return `${root}-${index}`;
}

export function credentialEnvFor(connectionKey) {
  const name = connectionKey.toUpperCase().replace(/[^A-Z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  return `${name || "PROVIDER"}_API_KEY`;
}

// Protocols the UI can offer for a connection: the provider's catalog
// protocols, or every known protocol for custom providers.
export function protocolChoices(provider, catalogs) {
  const known = catalogs?.providers?.[provider]?.protocols;
  if (known) return Object.keys(known);
  const protocols = [];
  for (const item of Object.values(catalogs?.providers || {})) {
    for (const protocol of Object.keys(item.protocols || {})) if (!protocols.includes(protocol)) protocols.push(protocol);
  }
  for (const protocol of Object.keys(PROTOCOL_LABELS)) if (!protocols.includes(protocol)) protocols.push(protocol);
  return protocols;
}

// A connection for a built-in provider takes its catalog defaults; a custom
// provider starts with one empty OpenAI-compatible endpoint.
export function connectionForProvider(provider, connectionKey, catalogs) {
  const providerCatalog = catalogs?.providers?.[provider];
  if (!provider) {
    return { provider: "", credential: { env: credentialEnvFor(connectionKey) }, credential_source: "missing", protocols: {}, verify_ssl: true };
  }
  if (!providerCatalog) {
    return { provider: connectionKey, credential: { env: credentialEnvFor(connectionKey) }, credential_source: "missing", protocols: { openai_chat_completions: { base_url: "" } }, verify_ssl: true };
  }
  const protocols = Object.fromEntries(Object.entries(providerCatalog.protocols || {}).map(([key, item]) => [key, { base_url: item.base_url || "" }]));
  const defaultProtocol = providerCatalog.default_protocol || Object.keys(protocols)[0] || "";
  const apiKeyEnv = providerCatalog.protocols?.[defaultProtocol]?.api_key_env;
  return { provider, credential: apiKeyEnv ? { env: apiKeyEnv } : { none: true }, credential_source: "missing", protocols, verify_ssl: true };
}

export function defaultProtocolFor(connection, catalogs) {
  const preferred = catalogs?.providers?.[connection?.provider]?.default_protocol;
  const available = Object.keys(connection?.protocols || {});
  return preferred && available.includes(preferred) ? preferred : available[0] || "";
}

export function defaultCapabilities() {
  return {
    modalities: { input: ["text"], output: ["text"] },
    streaming: { output: ["text"] },
    tools: { call: true, choice: ["auto"], parallel: true },
    structured_output: { json_object: true, json_schema: false },
    reasoning: { supported: false, controllable: false },
    limits: { context_tokens: 128000, max_output_tokens: 8192 },
  };
}

export function catalogCapabilities(catalogs, provider, protocol, modelId) {
  const known = catalogs?.model_capabilities?.find((item) => item.provider === provider && item.protocol === protocol && item.model_id === modelId);
  return known?.capabilities ? structuredClone(known.capabilities) : null;
}

// A new model record for one connection; capabilities come from the Pygent
// catalog when it knows the model and from the agentic default otherwise.
export function newModel(connectionKey, connection, modelId, protocol, catalogs) {
  return {
    connection: connectionKey,
    model_id: modelId,
    protocol,
    provider_options: {},
    capabilities: catalogCapabilities(catalogs, connection?.provider, protocol, modelId) || defaultCapabilities(),
  };
}

export function modelDiscoveryRequest(connection, protocol, credentialValue) {
  if (!connection || !protocol) return null;
  const { credential_source: _source, ...nativeConnection } = connection;
  return {
    protocol,
    connection: nativeConnection,
    credential_value: connection.credential?.env ? credentialValue || undefined : undefined,
  };
}

// One-line description of what a model can do, for collapsed model rows and
// group members.
export function capabilitySummary(capabilities) {
  const parts = [];
  const inputs = (capabilities?.modalities?.input || []).filter((item) => item !== "text");
  const outputs = (capabilities?.modalities?.output || []).filter((item) => item !== "text" && item !== "embedding");
  const labels = { image: "图片", audio: "音频", video: "视频" };
  if (capabilities?.tools?.call) parts.push("工具调用");
  if (capabilities?.reasoning?.supported) parts.push("推理");
  if (inputs.length) parts.push(`输入${inputs.map((item) => labels[item] || item).join("/")}`);
  if (outputs.length) parts.push(`输出${outputs.map((item) => labels[item] || item).join("/")}`);
  const context = capabilities?.limits?.context_tokens;
  if (Number.isFinite(context) && context > 0) parts.push(context >= 1000 ? `${Math.round(context / 1000)}K 上下文` : `${context} 上下文`);
  return parts.join(" · ") || "未声明能力";
}

// ---------------------------------------------------------------------------
// Connection editor buffer: { key, connection, models, renames, credentialValue }
// `renames` maps saved model key → current key for models renamed in place.

export function connectionBuffer(connectionKey, draft) {
  const connection = draft.connections[connectionKey];
  const models = Object.fromEntries(Object.entries(draft.models).filter(([, model]) => model.connection === connectionKey));
  return { key: connectionKey, connection: structuredClone(connection), models: structuredClone(models), renames: {}, credentialValue: "" };
}

export function newConnectionBuffer(provider, draft, catalogs) {
  const key = uniqueKey(provider || "provider", draft.connections);
  return { key, connection: connectionForProvider(provider, key, catalogs), models: {}, renames: {}, credentialValue: "" };
}

export function originalModelKey(renames, key) {
  return Object.keys(renames).find((original) => renames[original] === key) || key;
}

export function renameBufferModel(buffer, previousKey, nextKey) {
  const key = nextKey.trim();
  if (!key || key === previousKey || Object.hasOwn(buffer.models, key)) return buffer;
  const models = Object.fromEntries(Object.entries(buffer.models).map(([name, model]) => [name === previousKey ? key : name, model]));
  const original = originalModelKey(buffer.renames, previousKey);
  const { [original]: _dropped, ...renames } = buffer.renames;
  if (original !== key) renames[original] = key;
  return { ...buffer, models, renames };
}

export function removeBufferModel(buffer, modelKey) {
  const { [modelKey]: _removed, ...models } = buffer.models;
  const original = originalModelKey(buffer.renames, modelKey);
  const { [original]: _rename, ...renames } = buffer.renames;
  return { ...buffer, models, renames };
}

// Switching providers re-applies the catalog defaults and moves models that
// used a protocol the new provider lacks onto its default protocol.
export function switchBufferProvider(buffer, provider, catalogs) {
  const next = connectionForProvider(provider, buffer.key, catalogs);
  const previousEnv = buffer.connection.credential?.env;
  const connection = { ...buffer.connection, provider: next.provider, protocols: next.protocols, credential: next.credential, credential_source: next.credential?.env === previousEnv ? buffer.connection.credential_source : "missing" };
  const fallback = defaultProtocolFor(connection, catalogs);
  const models = Object.fromEntries(Object.entries(buffer.models).map(([key, model]) => [key, connection.protocols[model.protocol] ? model : { ...model, protocol: fallback }]));
  return { ...buffer, connection, models };
}

export function connectionError(buffer, savedDraft, previousKey) {
  const key = buffer.key.trim();
  if (!key) return "请填写连接名称。";
  if (key !== previousKey && Object.hasOwn(savedDraft.connections, key)) return "连接名称已存在。";
  const connection = buffer.connection;
  if (!connection.provider?.trim()) return "请选择服务商。";
  const protocols = Object.entries(connection.protocols || {});
  if (!protocols.length) return "至少需要一个接口。";
  if (protocols.some(([, endpoint]) => !endpoint?.base_url?.trim())) return "请填写每个接口的 Base URL。";
  if (!connection.credential?.none && !connection.credential?.env?.trim()) return "请填写凭据名称，或切换为无需认证。";
  if (!connection.credential?.none && connection.credential_source === "missing" && !buffer.credentialValue.trim()) return "请填写 API Key。";
  for (const [modelKey, model] of Object.entries(buffer.models)) {
    if (!model.model_id?.trim()) return `模型 ${modelKey} 缺少模型 ID。`;
    if (!connection.protocols[model.protocol]) return `模型 ${modelKey} 使用的协议不在此连接的接口中。`;
  }
  return "";
}

export function connectionDirty(buffer, savedDraft, previousKey) {
  if (!previousKey) return true;
  if (buffer.credentialValue.trim()) return true;
  return JSON.stringify(connectionBuffer(previousKey, savedDraft)) !== JSON.stringify({ ...buffer, renames: {}, credentialValue: "" });
}

// Commit: replace the connection and its model slice, remap renamed model
// keys inside every group, and stage the typed API key for the request.
export function commitConnection(savedDraft, previousKey, buffer) {
  const key = buffer.key.trim();
  const connections = { ...savedDraft.connections };
  if (previousKey && previousKey !== key) delete connections[previousKey];
  connections[key] = structuredClone(buffer.connection);
  const models = Object.fromEntries(Object.entries(savedDraft.models).filter(([, model]) => model.connection !== previousKey));
  for (const [modelKey, model] of Object.entries(buffer.models)) models[modelKey] = { ...structuredClone(model), connection: key };
  const modelGroups = Object.fromEntries(Object.entries(savedDraft.modelGroups).map(([name, group]) => [
    name,
    { models: group.models.map((member) => buffer.renames[member] || member).filter((member) => Object.hasOwn(models, member)) },
  ]));
  const env = buffer.connection.credential?.env;
  const credentialValues = env && buffer.credentialValue.trim() ? { ...savedDraft.credentialValues, [env]: buffer.credentialValue.trim() } : savedDraft.credentialValues;
  return { ...savedDraft, connections, models, modelGroups: dropEmptyGroups(modelGroups), credentialValues, defaultModelGroup: pickDefault(savedDraft.defaultModelGroup, dropEmptyGroups(modelGroups)) };
}

export function commitConnectionDelete(savedDraft, connectionKey) {
  const { [connectionKey]: _removed, ...connections } = savedDraft.connections;
  const models = Object.fromEntries(Object.entries(savedDraft.models).filter(([, model]) => model.connection !== connectionKey));
  return withModels({ ...savedDraft, connections }, models);
}

export function commitModelDelete(savedDraft, modelKey) {
  const { [modelKey]: _removed, ...models } = savedDraft.models;
  return withModels(savedDraft, models);
}

function withModels(draft, models) {
  const modelGroups = dropEmptyGroups(Object.fromEntries(Object.entries(draft.modelGroups).map(([name, group]) => [name, { models: group.models.filter((member) => Object.hasOwn(models, member)) }])));
  return { ...draft, models, modelGroups, defaultModelGroup: pickDefault(draft.defaultModelGroup, modelGroups) };
}

function dropEmptyGroups(groups) {
  return Object.fromEntries(Object.entries(groups).filter(([, group]) => group.models.length));
}

function pickDefault(current, groups) {
  return Object.hasOwn(groups, current) ? current : Object.keys(groups)[0] || "";
}

// ---------------------------------------------------------------------------
// Model group editor buffer: { name, models }

export function groupError(buffer, savedDraft, previousName) {
  const name = buffer.name.trim();
  if (!name) return "请填写模型组名称。";
  if (name !== previousName && Object.hasOwn(savedDraft.modelGroups, name)) return "模型组名称已存在。";
  if (!buffer.models.length) return "请选择主模型。";
  const unknown = buffer.models.find((key) => !Object.hasOwn(savedDraft.models, key));
  if (unknown) return `模型 ${unknown} 已不存在。`;
  if (new Set(buffer.models).size !== buffer.models.length) return "同一个模型只能出现一次。";
  return "";
}

export function groupDirty(buffer, savedDraft, previousName) {
  if (!previousName) return true;
  return buffer.name.trim() !== previousName || JSON.stringify(buffer.models) !== JSON.stringify(savedDraft.modelGroups[previousName]?.models || []);
}

export function commitGroup(savedDraft, previousName, buffer) {
  const name = buffer.name.trim();
  const modelGroups = { ...savedDraft.modelGroups };
  if (previousName && previousName !== name) delete modelGroups[previousName];
  modelGroups[name] = { models: [...buffer.models] };
  const defaultModelGroup = !savedDraft.defaultModelGroup || savedDraft.defaultModelGroup === previousName ? name : savedDraft.defaultModelGroup;
  return { ...savedDraft, modelGroups, defaultModelGroup };
}

export function commitGroupDelete(savedDraft, name) {
  const { [name]: _removed, ...modelGroups } = savedDraft.modelGroups;
  return { ...savedDraft, modelGroups, defaultModelGroup: pickDefault(savedDraft.defaultModelGroup, modelGroups) };
}

// Describe what deleting a model, connection or group does to the chats that
// use it, from the backend's usage counts. Mirrors the server's rebind rule:
// a removed model falls back to its group's next model, a removed group to
// the default group.
export function deletionImpact(kind, key, savedDraft, usage) {
  if (!usage) return "";
  if (kind === "group") {
    const count = usage.groups?.[key] || 0;
    if (!count) return "";
    const target = savedDraft.defaultModelGroup !== key ? savedDraft.defaultModelGroup : Object.keys(savedDraft.modelGroups).find((name) => name !== key) || "";
    return target ? `${count} 个对话正在使用此模型组，删除后它们会改用「${target}」。` : `${count} 个对话正在使用此模型组，删除后它们会在下次打开时重新选择模型。`;
  }
  const modelKeys = kind === "connection" ? Object.keys(savedDraft.models).filter((modelKey) => savedDraft.models[modelKey].connection === key) : [key];
  const count = modelKeys.reduce((total, modelKey) => total + (usage.models?.[modelKey] || 0), 0);
  if (!count) return "";
  const emptied = Object.entries(savedDraft.modelGroups).filter(([, group]) => group.models.every((member) => modelKeys.includes(member))).map(([name]) => name);
  const groupNote = emptied.length ? `模型组「${emptied.join("」「")}」会随之删除。` : "";
  return `${count} 个对话正在使用${kind === "connection" ? "此连接下的模型" : "此模型"}，删除后它们会改用所在模型组的下一个模型。${groupNote}`;
}
