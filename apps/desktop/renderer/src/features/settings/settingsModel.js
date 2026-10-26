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

const PROTOCOL_LABELS = {
  openai_chat_completions: "OpenAI Chat Completions",
  openai_responses: "OpenAI Responses",
  anthropic_messages: "Anthropic Messages",
  gemini_generate_content: "Gemini Generate Content",
};

export function protocolLabel(protocol) {
  return PROTOCOL_LABELS[protocol] || protocol || "请选择协议";
}

export function modelDiscoveryRequest(draft, connectionKey) {
  const connection = draft.connections?.[connectionKey];
  const protocol = Object.keys(connection?.protocols || {})[0];
  if (!connection || !protocol) return null;
  const envName = connection.credential?.env;
  const { credential_source: _source, ...nativeConnection } = connection;
  return {
    protocol,
    connection: nativeConnection,
    credential_value: envName ? draft.credentialValues?.[envName] || undefined : undefined,
  };
}

export function providerSelectionValue(provider, catalogs) {
  if (!catalogs || catalogs.providers?.[provider]) return provider || "";
  return "__custom__";
}

export function renamedConnectionValue(connection, previousKey, nextKey, catalogs) {
  const customProvider = connection.provider === previousKey || Boolean(catalogs && !catalogs.providers?.[connection.provider]);
  return customProvider ? { ...connection, provider: nextKey } : connection;
}

export function protocolChoicesForConnection(provider, catalogs) {
  const known = catalogs?.providers?.[provider]?.protocols;
  if (known) return Object.keys(known);
  const protocols = [];
  for (const item of Object.values(catalogs?.providers || {})) {
    for (const protocol of Object.keys(item.protocols || {})) {
      if (!protocols.includes(protocol)) protocols.push(protocol);
    }
  }
  for (const protocol of Object.keys(PROTOCOL_LABELS)) {
    if (!protocols.includes(protocol)) protocols.push(protocol);
  }
  return protocols;
}

export function capabilityPresetLabel(name) {
  return String(name || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function emptyNativeConnection(id) {
  return {
    provider: "openai",
    credential: { env: "OPENAI_API_KEY" },
    protocols: {
      openai_responses: { base_url: "https://api.openai.com/v1" },
      openai_chat_completions: { base_url: "https://api.openai.com/v1" },
    },
    verify_ssl: true,
  };
}

export function emptyNativeModel(connection, protocol) {
  return {
    connection,
    model_id: "",
    protocol,
    provider_options: {},
    capabilities: {
      modalities: { input: ["text"], output: ["text"] },
      streaming: { output: ["text"] },
      tools: { call: true, choice: ["auto"], parallel: true },
      structured_output: { json_object: true, json_schema: false },
      reasoning: { supported: false, controllable: false },
      limits: { context_tokens: 128000, max_output_tokens: 8192 },
    },
  };
}

export function nextConnectionKey(connections) {
  let index = Object.keys(connections).length + 1;
  while (Object.hasOwn(connections, `供应商${index}`)) index += 1;
  return `供应商${index}`;
}

export function nextModelKey(models) {
  let index = Object.keys(models).length + 1;
  while (Object.hasOwn(models, `model-${index}`)) index += 1;
  return `model-${index}`;
}

export function modelGroupValidationError(draft) {
  const connections = draft.connections || {};
  const models = draft.models || {};
  const groups = draft.modelGroups || {};
  if (!Object.keys(connections).length) return "请至少添加一个连接。";
  for (const [key, connection] of Object.entries(connections)) {
    if (!key.trim() || !connection?.provider?.trim()) return `连接 ${key || "未命名"} 的信息不完整。`;
    const protocols = connection.protocols || {};
    if (!Object.keys(protocols).length || Object.values(protocols).some((endpoint) => !endpoint?.base_url?.trim())) return `连接 ${key} 至少需要一个填写了地址的协议端点。`;
    const credential = connection.credential;
    if (!credential || (!credential.none && !credential.env?.trim())) return `连接 ${key} 需要凭据名称或“无需认证”设置。`;
  }
  if (!Object.keys(models).length) return "请至少添加一个模型。";
  for (const [key, model] of Object.entries(models)) {
    if (!key.trim() || !model?.connection?.trim() || !model?.model_id?.trim() || !model?.protocol?.trim()) return `模型 ${key || "未命名"} 的信息不完整，请填写连接、协议和模型 ID。`;
    const connection = connections[model.connection];
    if (!connection) return `模型 ${key} 引用了不存在的连接 ${model.connection}。`;
    if (!connection.protocols?.[model.protocol]) return `模型 ${key} 选择的协议未在连接 ${model.connection} 中配置。`;
    if (!model.capabilities?.limits) return `模型 ${key} 缺少能力配置，请选择能力模板。`;
  }
  if (!Object.keys(groups).length) return "请至少添加一个模型组。";
  for (const [name, group] of Object.entries(groups)) {
    if (!name.trim() || !Array.isArray(group?.models) || !group.models.length) return `模型组 ${name || "未命名"} 至少需要选择一个模型。`;
    const unknown = group.models.find((key) => !Object.hasOwn(models, key));
    if (unknown) return `模型组 ${name} 引用了不存在的模型 ${unknown}。`;
  }
  if (!Object.hasOwn(groups, draft.defaultModelGroup || "")) return "请选择一个已有模型组作为默认模型组。";
  return "";
}

export function settingsForSave(draft, settings) {
  const workspaceChanged = draft.workspaceRoot.trim() !== (settings.workspace_root || "").trim();
  const keptPreviousAgent = draft.agent.trim() === (settings.agent || "").trim();
  return workspaceChanged && keptPreviousAgent ? { ...draft, agent: "" } : draft;
}


// Merge a connection editor buffer back into the draft: replaces the
// connection entry and its model slice, remaps renamed model keys inside
// model groups and drops models deleted in the buffer. `previousKey` is null
// when the buffer creates a new connection.
export function commitConnectionBuffer(draft, previousKey, buffer, catalogs) {
  const key = buffer.key;
  const oldKeys = previousKey ? Object.keys(draft.models).filter((modelKey) => draft.models[modelKey].connection === previousKey) : [];
  const finalKey = (modelKey) => buffer.renames?.[modelKey] || modelKey;
  const bufferKeys = new Set(Object.keys(buffer.models));
  const removedFinal = new Set(oldKeys.filter((modelKey) => !bufferKeys.has(finalKey(modelKey))).map(finalKey));
  const modelGroups = Object.fromEntries(Object.entries(draft.modelGroups).map(([name, group]) => [
    name,
    { models: group.models.map((modelKey) => finalKey(modelKey)).filter((modelKey) => !removedFinal.has(modelKey)) },
  ]));
  const models = Object.fromEntries(Object.entries(draft.models).filter(([modelKey]) => !oldKeys.includes(modelKey)));
  for (const [modelKey, model] of Object.entries(buffer.models)) models[modelKey] = { ...model, connection: key };
  const connections = { ...draft.connections };
  if (previousKey && previousKey !== key) delete connections[previousKey];
  connections[key] = renamedConnectionValue(buffer.connection, previousKey, key, catalogs);
  return { ...draft, connections, models, modelGroups, credentialValues: { ...draft.credentialValues, ...buffer.credentialValues } };
}

// The saved key a buffer row commits against: a pending rename points back at
// the entry it replaces, everything else commits under its own key.
export function originalModelKey(renames, key) {
  for (const [original, renamed] of Object.entries(renames || {})) {
    if (renamed === key) return original;
  }
  return key;
}

// Saves are sliced inside one connection editor: connection fields (plus the
// connection-owned protocol remaps) commit through the head save, and each
// model commits through its own row save, so an in-progress model never
// blocks or rides along with another one.
export function connectionFieldsDirty(savedDraft, previousKey, buffer) {
  if (!previousKey) return true;
  const savedConnection = savedDraft.connections[previousKey];
  if (!savedConnection) return true;
  const env = buffer.connection.credential?.env;
  const savedValue = env ? savedDraft.credentialValues[env] || "" : "";
  const bufferValue = env ? buffer.credentialValues[env] || "" : "";
  return JSON.stringify(savedConnection) !== JSON.stringify(buffer.connection) || bufferValue !== savedValue;
}

export function modelRowDirty(savedDraft, buffer, key) {
  const original = originalModelKey(buffer.renames, key);
  if (original !== key) return true;
  const saved = savedDraft.models[key];
  if (!saved) return true;
  const { connection: _savedConnection, ...savedRest } = saved;
  const { connection: _bufferConnection, ...modelRest } = buffer.models[key] || {};
  return JSON.stringify(savedRest) !== JSON.stringify(modelRest);
}

export function connectionSliceDirty(savedDraft, previousKey, buffer) {
  if (connectionFieldsDirty(savedDraft, previousKey, buffer)) return true;
  return Object.keys(buffer.models).some((key) => modelRowDirty(savedDraft, buffer, key));
}

// Head save: connection fields only. Models keep their saved entries; the
// protocol is connection-owned (endpoints define which protocols exist), so
// a protocol change commits the remapped models with the connection.
export function commitConnectionFields(savedDraft, previousKey, buffer, catalogs) {
  const key = buffer.key;
  const connections = { ...savedDraft.connections };
  if (previousKey && previousKey !== key) delete connections[previousKey];
  connections[key] = renamedConnectionValue(buffer.connection, previousKey, key, catalogs);
  const models = {};
  for (const [savedKey, savedModel] of Object.entries(savedDraft.models)) {
    if (savedModel.connection !== previousKey) {
      models[savedKey] = savedModel;
      continue;
    }
    const bufferKey = Object.keys(buffer.models).find((candidate) => originalModelKey(buffer.renames, candidate) === savedKey);
    models[savedKey] = {
      ...savedModel,
      connection: key,
      ...(bufferKey && buffer.models[bufferKey] ? { protocol: buffer.models[bufferKey].protocol } : {}),
    };
  }
  return { ...savedDraft, connections, models };
}

// Row save: add or update one model entry. A pending rename also remaps the
// key inside every model group in the same commit.
export function commitModelSave(savedDraft, previousKey, buffer, catalogs, key) {
  const connectionKey = buffer.key;
  const original = originalModelKey(buffer.renames, key);
  const models = { ...savedDraft.models };
  if (original !== key) delete models[original];
  models[key] = { ...buffer.models[key], connection: connectionKey };
  const modelGroups = original === key ? savedDraft.modelGroups : Object.fromEntries(Object.entries(savedDraft.modelGroups).map(([name, group]) => [
    name,
    { models: group.models.map((member) => (member === original ? key : member)) },
  ]));
  return { ...savedDraft, models, modelGroups };
}

// Row save of a deletion-marked model: drops the saved entry and scrubs it
// from every group; groups left empty disappear with it. If the default
// group is affected, the server's default_model_group guard explains itself.
export function commitModelDelete(savedDraft, previousKey, buffer, catalogs, key) {
  const original = originalModelKey(buffer.renames, key);
  const models = { ...savedDraft.models };
  delete models[original];
  const modelGroups = Object.fromEntries(Object.entries(savedDraft.modelGroups)
    .map(([name, group]) => [name, { models: group.models.filter((member) => member !== original) }])
    .filter(([, group]) => group.models.length));
  return { ...savedDraft, models, modelGroups };
}

export function changeConnectionProtocol(draft, connectionKey, previous, next) {
  const connection = draft.connections[connectionKey];
  if (!connection || previous === next || !PROTOCOL_LABELS[next] || Object.hasOwn(connection.protocols || {}, next)) return draft;
  const endpoint = connection.protocols?.[previous];
  if (!endpoint) return draft;
  const protocols = Object.fromEntries(Object.entries(connection.protocols).map(([key, value]) => [key === previous ? next : key, value]));
  const models = Object.fromEntries(Object.entries(draft.models).map(([key, model]) => [key, model.connection === connectionKey && model.protocol === previous ? { ...model, protocol: next } : model]));
  return { ...draft, connections: { ...draft.connections, [connectionKey]: { ...connection, protocols } }, models };
}

export function applyBuiltinProvider(draft, connectionKey, provider, providerCatalog) {
  const connection = draft.connections[connectionKey];
  if (!connection || !providerCatalog) return draft;
  const protocols = Object.fromEntries(Object.entries(providerCatalog.protocols || {}).map(([key, item]) => [key, { base_url: item.base_url || "" }]));
  const defaultProtocol = providerCatalog.default_protocol || Object.keys(protocols)[0] || "";
  const apiKeyEnv = providerCatalog.protocols?.[defaultProtocol]?.api_key_env;
  const previousEnv = connection.credential?.env;
  const credential = apiKeyEnv ? { env: apiKeyEnv } : { none: true };
  const credentialSource = apiKeyEnv === previousEnv ? connection.credential_source : "missing";
  const connections = { ...draft.connections, [connectionKey]: { ...connection, provider, protocols, credential, credential_source: credentialSource } };
  const models = Object.fromEntries(Object.entries(draft.models).map(([key, model]) => [key, model.connection === connectionKey && !protocols[model.protocol] ? { ...model, protocol: defaultProtocol } : model]));
  const previousValue = previousEnv ? draft.credentialValues[previousEnv] : "";
  const credentialValues = previousValue && apiKeyEnv
    ? { ...draft.credentialValues, [apiKeyEnv]: draft.credentialValues[apiKeyEnv] || previousValue }
    : draft.credentialValues;
  return { ...draft, connections, models, credentialValues };
}
