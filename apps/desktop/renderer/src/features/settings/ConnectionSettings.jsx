import React, { useCallback, useEffect, useRef, useState } from "react";
import { ChevronRight, Plus, Server, Trash2 } from "lucide-react";
import { AddModelForm } from "./AddModelForm.jsx";
import { ModelCapabilitiesEditor } from "./ModelCapabilitiesEditor.jsx";
import {
  changeConnectionProtocol,
  emptyNativeConnection,
  emptyNativeModel,
  modelDiscoveryRequest,
  nextConnectionKey,
  nextModelKey,
  protocolChoicesForConnection,
  protocolLabel,
  providerSelectionValue,
} from "./settingsModel.js";

const TEST_STATUS_TEXT = {
  empty: "连接可用，但服务未返回模型列表",
  error: "连接失败，请检查地址与密钥",
};

// Model entries without the connection back-reference, so a buffer can be
// compared against the saved slice by value.
function modelEntriesWithoutConnection(models) {
  return Object.fromEntries(Object.entries(models).map(([modelKey, model]) => {
    const { connection: _container, ...rest } = model;
    return [modelKey, rest];
  }));
}

function bufferIsDirty(savedDraft, previousKey, buffer) {
  const savedConnection = previousKey ? savedDraft.connections[previousKey] : null;
  const savedModels = previousKey ? modelEntriesWithoutConnection(Object.fromEntries(Object.entries(savedDraft.models).filter(([, model]) => model.connection === previousKey))) : {};
  const env = buffer.connection.credential?.env;
  const savedValue = previousKey && env ? savedDraft.credentialValues[env] || "" : "";
  const bufferValue = env ? buffer.credentialValues[env] || "" : "";
  return JSON.stringify(savedConnection) !== JSON.stringify(buffer.connection)
    || JSON.stringify(savedModels) !== JSON.stringify(modelEntriesWithoutConnection(buffer.models))
    || bufferValue !== savedValue;
}

// The first problem with an unsaved buffer, or "" when the buffer can be saved.
function bufferSaveError(savedDraft, previousKey, buffer) {
  if (!buffer.key.trim()) return "请填写连接名称。";
  if (Object.hasOwn(savedDraft.connections, buffer.key.trim()) && buffer.key.trim() !== previousKey) return "连接名称已存在。";
  const connection = buffer.connection;
  const protocols = Object.entries(connection.protocols || {});
  if (!connection.provider) return "请选择供应商。";
  if (!protocols.length || protocols.some(([, endpoint]) => !endpoint?.base_url?.trim())) return "请填写 Base URL。";
  if (!connection.credential?.none && !connection.credential?.env?.trim()) return "连接需要凭据名称或“无需认证”设置。";
  if (!connection.credential?.none && connection.credential_source === "missing" && !buffer.credentialValues[connection.credential.env]?.trim()) return "请填写 API Key。";
  for (const [modelKey, model] of Object.entries(buffer.models)) {
    if (!model.model_id?.trim()) return `模型 ${modelKey} 缺少模型 ID。`;
  }
  return "";
}

// One connection editor with a local buffer and its own save button, so an
// unsaved connection never leaks into another connection's save. `savedDraft`
// is the last persisted state: validity, dirty checks and key generation all
// compare against it, not against the live draft with its pending edits.
export function ConnectionEditor({ connectionKey, previousKey, connection, models, catalogs, api, savedDraft, collapse, onPersist, onDirtyChange }) {
  const [buffer, setBuffer] = useState(() => ({
    key: connectionKey,
    connection: structuredClone(connection),
    models: structuredClone(models),
    renames: {},
    credentialValues: previousKey && connection.credential?.env ? { [connection.credential.env]: savedDraft.credentialValues[connection.credential.env] || "" } : {},
  }));
  const [testState, setTestState] = useState(null);
  const [addingModel, setAddingModel] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [expandedModel, setExpandedModel] = useState(() => Object.entries(models).find(([, model]) => !model.model_id?.trim())?.[0] || null);
  const [modelDiscovered, setModelDiscovered] = useState({});
  const [modelDiscoveryState, setModelDiscoveryState] = useState({});
  // Built-in providers collapse the endpoint row into a catalog-default caption
  // until the user asks for a custom address; the choice is per connection.
  const [customAddress, setCustomAddress] = useState({});

  const connectionWithoutSource = (({ credential_source: _source, ...rest }) => rest)(buffer.connection);
  const provider = buffer.connection.provider;
  const protocols = Object.entries(buffer.connection.protocols || {});
  const credentialEnv = buffer.connection.credential?.env || "";
  const authMode = buffer.connection.credential?.none ? "none" : "api-key";
  const providerSelection = providerSelectionValue(provider, catalogs);
  const isCustomProvider = providerSelection === "__custom__";
  // "已配置" covers a saved credential_source and a key typed in this editor
  // session; the backend only confirms the source after the next full reload.
  const savedKey = Boolean((buffer.connection.credential_source && buffer.connection.credential_source !== "missing") || buffer.credentialValues[credentialEnv]?.trim());
  const protocolChoices = protocolChoicesForConnection("__custom__", catalogs);
  const addableProtocols = protocolChoices.filter((protocol) => !buffer.connection.protocols?.[protocol]);
  const firstProtocol = protocols[0]?.[0];
  const catalogDefault = catalogs?.providers?.[provider]?.protocols?.[firstProtocol]?.base_url || "";
  const matchesCatalogDefault = protocols.length === 1 && Boolean(catalogDefault) && protocols[0][1]?.base_url === catalogDefault;
  const addressEditorOpen = isCustomProvider || Boolean(customAddress[connectionKey]) || !matchesCatalogDefault;
  // Existing editors compare against the saved slice; the create form counts
  // as dirty only after the user changes something, so an untouched abandoned
  // form never trips the close guard.
  const initialBufferJsonRef = useRef(null);
  if (initialBufferJsonRef.current === null) initialBufferJsonRef.current = JSON.stringify(buffer);
  const dirty = previousKey
    ? bufferIsDirty(savedDraft, previousKey, buffer)
    : JSON.stringify(buffer) !== initialBufferJsonRef.current;

  useEffect(() => {
    onDirtyChange?.(connectionKey, dirty);
    return () => onDirtyChange?.(connectionKey, false);
  }, [dirty, connectionKey, onDirtyChange]);

  const missing = bufferSaveError(savedDraft, previousKey, buffer);

  function updateConnection(field, value) {
    setBuffer((current) => ({ ...current, connection: { ...current.connection, [field]: value } }));
  }

  function updateModel(modelKey, field, value) {
    setBuffer((current) => {
      const model = current.models[modelKey];
      if (!model) return current;
      if (field === "context_tokens" || field === "max_output_tokens") {
        return { ...current, models: { ...current.models, [modelKey]: { ...model, capabilities: { ...model.capabilities, limits: { ...model.capabilities?.limits, [field]: Number(value) || null } } } } };
      }
      return { ...current, models: { ...current.models, [modelKey]: { ...model, [field]: value } } };
    });
  }

  function updateModelId(modelKey, modelId) {
    setBuffer((current) => {
      const model = current.models[modelKey];
      if (!model) return current;
      const known = catalogs?.model_capabilities?.find((item) => item.provider === provider && item.protocol === model.protocol && item.model_id === modelId);
      return { ...current, models: { ...current.models, [modelKey]: { ...model, model_id: modelId, ...(known?.capabilities ? { capabilities: structuredClone(known.capabilities) } : {}) } } };
    });
  }

  function renameModelKey(previousModelKey, nextKey) {
    const key = nextKey.trim();
    if (!key || key === previousModelKey || Object.hasOwn(buffer.models, key)) return;
    setBuffer((current) => {
      const models = {};
      for (const [name, model] of Object.entries(current.models)) models[name === previousModelKey ? key : name] = model;
      // Keep the original key so commit can remap model-group membership.
      const renamedFrom = Object.keys(current.renames).find((original) => current.renames[original] === previousModelKey) || previousModelKey;
      return { ...current, models, renames: { ...current.renames, [renamedFrom]: key } };
    });
    setExpandedModel((current) => current === previousModelKey ? key : current);
  }

  function removeModel(modelKey) {
    setBuffer((current) => {
      const { [modelKey]: _removed, ...models } = current.models;
      const renames = { ...current.renames };
      for (const [original, renamed] of Object.entries(renames)) if (renamed === modelKey) delete renames[original];
      return { ...current, models, renames };
    });
    setExpandedModel((current) => current === modelKey ? null : current);
  }

  function addModel(modelId) {
    const protocol = Object.keys(buffer.connection.protocols || {})[0];
    if (!protocol) return;
    const modelKey = nextModelKey({ ...savedDraft.models, ...buffer.models });
    const known = catalogs?.model_capabilities?.find(item => item.provider === provider && item.protocol === protocol && item.model_id === modelId);
    const model = { ...emptyNativeModel(buffer.key, protocol), model_id: modelId, ...(known?.capabilities ? { capabilities: structuredClone(known.capabilities) } : {}) };
    setBuffer((current) => ({ ...current, models: { ...current.models, [modelKey]: model } }));
    setAddingModel(false);
    setExpandedModel(modelKey);
  }

  function selectProvider(value) {
    if (!value) return;
    setCustomAddress({});
    if (value === "__custom__") {
      setBuffer((current) => ({ ...current, connection: { ...current.connection, provider: current.key } }));
      return;
    }
    setBuffer((current) => {
      const providerCatalog = catalogs?.providers?.[value];
      if (!providerCatalog) return { ...current, connection: { ...current.connection, provider: value } };
      const nextProtocols = Object.fromEntries(Object.entries(providerCatalog.protocols || {}).map(([key, item]) => [key, { base_url: item.base_url || "" }]));
      const defaultProtocol = providerCatalog.default_protocol || Object.keys(nextProtocols)[0] || "";
      const apiKeyEnv = providerCatalog.protocols?.[defaultProtocol]?.api_key_env;
      const previousEnv = current.connection.credential?.env;
      const previousValue = previousEnv ? current.credentialValues[previousEnv] : "";
      const credentialValues = previousValue && apiKeyEnv
        ? { ...current.credentialValues, [apiKeyEnv]: current.credentialValues[apiKeyEnv] || previousValue }
        : current.credentialValues;
      return {
        ...current,
        connection: {
          ...current.connection,
          provider: value,
          protocols: nextProtocols,
          credential: apiKeyEnv ? { env: apiKeyEnv } : { none: true },
          credential_source: apiKeyEnv === previousEnv ? current.connection.credential_source : "missing",
        },
        models: Object.fromEntries(Object.entries(current.models).map(([key, model]) => [key, nextProtocols[model.protocol] ? model : { ...model, protocol: defaultProtocol }])),
        credentialValues,
      };
    });
  }

  function renameConnectionKey(nextKey) {
    const key = nextKey.trim();
    if (!key || key === buffer.key) return;
    if (Object.hasOwn(savedDraft.connections, key) && key !== previousKey) {
      setSaveError("连接名称已存在。");
      return;
    }
    setSaveError("");
    setBuffer((current) => ({
      ...current,
      key,
      connection: { ...current.connection, ...(providerSelection === "__custom__" ? { provider: key } : {}) },
      models: Object.fromEntries(Object.entries(current.models).map(([modelKey, model]) => [modelKey, { ...model, connection: key }])),
    }));
  }

  async function testConnection() {
    const request = modelDiscoveryRequest({ connections: { [buffer.key]: connectionWithoutSource }, credentialValues: buffer.credentialValues }, buffer.key);
    if (!request || !api?.discoverModels) return;
    setTestState({ status: "loading" });
    try {
      const response = await api.discoverModels(request);
      const count = Array.isArray(response?.models) ? response.models.length : 0;
      setTestState({ status: count ? "ready" : "empty", count });
    } catch {
      setTestState({ status: "error" });
    }
  }

  async function discoverModelIds(modelKey) {
    const model = buffer.models[modelKey];
    if (!api?.discoverModels || !model) return;
    const request = modelDiscoveryRequest({ connections: { [buffer.key]: connectionWithoutSource }, credentialValues: buffer.credentialValues }, buffer.key);
    if (!request) return;
    setModelDiscoveryState((current) => ({ ...current, [modelKey]: { loading: true } }));
    try {
      const response = await api.discoverModels({ ...request, protocol: model.protocol });
      setModelDiscovered((current) => ({ ...current, [modelKey]: response.models || [] }));
      setModelDiscoveryState((current) => ({ ...current, [modelKey]: { message: response.models?.length ? `已获取 ${response.models.length} 个模型，请在模型 ID 输入框中选择。` : "未获取到模型，可手动填写模型 ID。" } }));
    } catch {
      setModelDiscoveryState((current) => ({ ...current, [modelKey]: { message: "获取失败，请检查连接地址与凭据，或手动填写模型 ID。" } }));
    }
  }

  function discardEdits() {
    const savedConnection = savedDraft.connections[previousKey];
    if (!savedConnection) return;
    const savedModels = Object.fromEntries(Object.entries(savedDraft.models).filter(([, model]) => model.connection === previousKey));
    setBuffer({
      key: previousKey,
      connection: structuredClone(savedConnection),
      models: structuredClone(savedModels),
      renames: {},
      credentialValues: savedConnection.credential?.env ? { [savedConnection.credential.env]: savedDraft.credentialValues[savedConnection.credential.env] || "" } : {},
    });
    setTestState(null);
    setAddingModel(false);
    setSaveError("");
    setCustomAddress({});
    setModelDiscovered({});
    setModelDiscoveryState({});
    setExpandedModel(Object.entries(savedModels).find(([, model]) => !model.model_id?.trim())?.[0] || null);
  }

  function cancelEditing() {
    if (previousKey) discardEdits();
    collapse();
  }

  async function save() {
    if (missing) return;
    setSaveError("");
    try {
      await onPersist(previousKey, buffer, catalogs);
      collapse();
    } catch (error) {
      setSaveError(typeof error?.message === "string" && error.message ? error.message : "保存失败，请稍后再试。");
    }
  }

  function protocolSelect(protocol) {
    return <label>
      <span>接口协议</span>
      <select value={protocol} onChange={(event) => setBuffer((current) => {
        const next = changeConnectionProtocol({ connections: { [current.key]: current.connection }, models: current.models, modelGroups: {}, credentialValues: current.credentialValues }, current.key, protocol, event.target.value);
        return { ...current, connection: next.connections[current.key], models: next.models };
      })}>
        {protocolChoices.map((key) => <option key={key} value={key} disabled={key !== protocol && Boolean(buffer.connection.protocols?.[key])}>{protocolLabel(key)}</option>)}
      </select>
    </label>;
  }

  // Body only: the collapsed/expanded row header is rendered by the parent so
  // both states share one identical head.
  return <div className="connection-route-card connection-editor">
    {missing && <div className="connection-missing" role="status">{missing}</div>}
    {saveError && <div className="connection-missing" role="status">{saveError}</div>}
    {testState && testState.status !== "loading" && <div className={`connection-test-status ${testState.status}`} role="status">{testState.status === "ready" ? `连接可用，发现 ${testState.count} 个模型` : TEST_STATUS_TEXT[testState.status]}</div>}

    <div className="connection-primary-fields">
      {isCustomProvider
        ? <label><span>供应商名称</span><input defaultValue={buffer.key} key={buffer.key} onBlur={(event) => renameConnectionKey(event.target.value)} /><small>同时是连接名称，仅供本地引用。</small></label>
        : <label>
            <span>服务商 Provider</span>
            <select value={providerSelection || ""} onChange={(event) => selectProvider(event.target.value)}>
              {!catalogs && <option value={provider}>{provider || "请选择"}</option>}
              {Object.entries(catalogs?.providers || {}).length > 0 && <optgroup label="Pygent 内置">{Object.entries(catalogs.providers).map(([key, item]) => <option key={key} value={key}>{item.display_name || key}</option>)}</optgroup>}
              <optgroup label="自定义"><option value="__custom__">自定义供应商</option></optgroup>
            </select>
            <small>选择服务商后，自动填写默认地址和支持的接口协议。</small>
          </label>}
      {!isCustomProvider && <label><span>连接名称</span><input defaultValue={buffer.key} key={buffer.key} onBlur={(event) => renameConnectionKey(event.target.value)} /><small>仅供本地引用，不会发送给服务商。</small></label>}
      {authMode === "api-key" && <label>
        <span>API Key <small className="field-required">{savedKey ? "已配置" : "必填"}</small></span>
        <input disabled={!credentialEnv} type="password" autoComplete="off" placeholder={savedKey ? "留空保留已有密钥" : "填写 API Key"} value={buffer.credentialValues[credentialEnv] || ""} onChange={(event) => setBuffer((current) => ({ ...current, credentialValues: { ...current.credentialValues, [credentialEnv]: event.target.value } }))} />
        <small className="connection-auth-alt">本地或免鉴权服务？<button type="button" className="link-action" onClick={() => updateConnection("credential", { none: true })}>切换为无需认证</button></small>
      </label>}
      {authMode === "none" && <label>
        <span>认证</span>
        <div className="connection-auth-none">此连接无需密钥 <button type="button" className="link-action" onClick={() => updateConnection("credential", { env: credentialEnv || `${buffer.key.toUpperCase().replace(/[^A-Z0-9]+/g, "_")}_API_KEY` })}>改用 API Key</button></div>
      </label>}
    </div>

    <div className="connection-endpoints">
      {addressEditorOpen ? <>
        <div className="connection-section-heading"><strong>接口配置</strong><span>一个连接可以提供多个协议接口</span></div>
        <div className="protocol-interface-list">
          {protocols.map(([protocol, endpoint]) => (
            <div className="protocol-interface-row" key={protocol}>
              <label><span>Base URL</span><input placeholder="https://api.example.com/v1" value={endpoint?.base_url || ""} onChange={(event) => setBuffer((current) => ({ ...current, connection: { ...current.connection, protocols: { ...current.connection.protocols, [protocol]: { base_url: event.target.value } } } }))} /></label>
              {protocolSelect(protocol)}
              <button
                className="route-remove"
                disabled={protocols.length === 1 || Object.values(buffer.models).some((model) => model.protocol === protocol)}
                type="button"
                onClick={() => setBuffer((current) => {
                  const { [protocol]: _removed, ...nextProtocols } = current.connection.protocols || {};
                  return { ...current, connection: { ...current.connection, protocols: nextProtocols } };
                })}
                aria-label={`Remove ${protocol}`}
                title={protocols.length === 1 ? "至少保留一个接口" : "删除接口"}
              ><Trash2 aria-hidden="true" /></button>
            </div>
          ))}
        </div>
        {addableProtocols.length > 0 && <label className="protocol-add-row">
          <span><Plus size={14} /> 添加接口</span>
          <select value="" onChange={(event) => setBuffer((current) => {
            const protocol = event.target.value;
            if (!protocol || current.connection.protocols?.[protocol]) return current;
            const baseUrl = catalogs?.providers?.[current.connection.provider]?.protocols?.[protocol]?.base_url || "";
            return { ...current, connection: { ...current.connection, protocols: { ...current.connection.protocols, [protocol]: { base_url: baseUrl } } } };
          })}>
            <option value="">选择接口协议…</option>
            {addableProtocols.map((protocol) => <option key={protocol} value={protocol}>{protocolLabel(protocol)}</option>)}
          </select>
        </label>}
      </> : <div className="connection-default-url">
        <span>使用官方默认地址 <code>{catalogDefault}</code> · {protocolLabel(firstProtocol)}</span>
        <button type="button" className="link-action" onClick={() => setCustomAddress((current) => ({ ...current, [connectionKey]: true }))}>改用自定义地址</button>
      </div>}
    </div>

    <div className="connection-models">
      <div className="connection-section-heading">
        <strong>模型管理</strong>
        <span>此连接下可用的模型</span>
        <button type="button" className="plain-action connection-add-model" disabled={addingModel || !protocols.length} onClick={() => setAddingModel(true)}><Plus size={14} aria-hidden="true" /> 添加模型</button>
      </div>
      {addingModel && <AddModelForm connection={buffer.connection} credentialValue={buffer.credentialValues[credentialEnv] || ""} api={api} onAdd={addModel} onCancel={() => setAddingModel(false)} />}
      {Object.entries(buffer.models).map(([modelKey, model]) => {
        const protocolSelectable = protocols.length > 1 || (Boolean(model.protocol) && !protocols.includes(model.protocol));
        return <details className="connection-model-details" key={modelKey} open={expandedModel === modelKey}>
          <summary className="model-route-title">
            <span className="connection-model-copy">
              <strong>{model.model_id || modelKey}</strong>
              <small>{model.model_id ? modelKey : "请填写模型 ID"} · {protocolLabel(model.protocol)}</small>
            </span>
            <button
              className="route-remove"
              type="button"
              onClick={(event) => { event.preventDefault(); event.stopPropagation(); removeModel(modelKey); }}
              aria-label={`Remove ${modelKey}`}
              title="删除模型"
            ><Trash2 aria-hidden="true" /></button>
          </summary>
          <div className="model-config-block">
            <div className="model-config-block-heading"><strong>模型信息</strong><span>决定调用谁、使用哪种 API 格式；不属于连接。</span></div>
            <div className="model-route-grid">
              <label><span>本地名称</span><input defaultValue={modelKey} key={modelKey} onBlur={(event) => renameModelKey(modelKey, event.target.value)} /><small>仅本地引用名称，不会发送给服务商。</small></label>
              <label><span>服务商模型 ID</span><input list={`models-${modelKey}`} placeholder="例如 gpt-5.1-codex" value={model.model_id || ""} onChange={(event) => updateModelId(modelKey, event.target.value)} /><small>这是服务商文档或“发现模型”返回的真实 ID。</small></label>
              <datalist id={`models-${modelKey}`}>{(modelDiscovered[modelKey] || []).map((item) => <option key={item.id} value={item.id} />)}</datalist>
              <button className="plain-action discover-action" disabled={!api?.discoverModels || modelDiscoveryState[modelKey]?.loading} type="button" onClick={() => discoverModelIds(modelKey)}>{modelDiscoveryState[modelKey]?.loading ? "正在获取模型…" : "通过连接发现模型 ID"}</button>
              {modelDiscoveryState[modelKey]?.message && <small className="route-secret" role="status">{modelDiscoveryState[modelKey].message}</small>}
              {protocolSelectable && <label><span>API 协议</span><select value={model.protocol || ""} onChange={(event) => updateModel(modelKey, "protocol", event.target.value)}>{!protocols.includes(model.protocol) && <option value={model.protocol}>{protocolLabel(model.protocol)}</option>}{protocols.map(([key]) => <option key={key} value={key}>{protocolLabel(key)}</option>)}</select><small>只能选择当前连接提供的协议。</small></label>}
            </div>
            <ModelCapabilitiesEditor value={model.capabilities} catalogs={catalogs} provider={provider} protocol={model.protocol || ""} onChange={(value) => updateModel(modelKey, "capabilities", value)} />
          </div>
        </details>;
      })}
      {!Object.keys(buffer.models).length && !addingModel && <p className="settings-inline-empty">此连接下还没有模型，点击“添加模型”。</p>}
    </div>

    <details className="connection-advanced settings-detail">
      <summary>高级配置 <span>认证、代理与 TLS</span></summary>
      <div className="model-route-grid">
        {authMode === "api-key" && <label><span>凭据名称</span><input value={credentialEnv} onChange={(event) => updateConnection("credential", event.target.value ? { env: event.target.value } : { none: true })} /><small>配置只保存名称，不保存密钥。</small></label>}
        <label><span>代理（可选）</span><input placeholder="http://127.0.0.1:7890" value={buffer.connection.proxy || ""} onChange={(event) => updateConnection("proxy", event.target.value)} /></label>
        <label><span>TLS 证书验证</span><select value={buffer.connection.verify_ssl === false ? "off" : "on"} onChange={(event) => updateConnection("verify_ssl", event.target.value === "on")}><option value="on">开启（推荐）</option><option value="off">关闭</option></select></label>
      </div>
    </details>

    <footer className="connection-editor-actions">
      <button type="button" className="plain-action" disabled={!modelDiscoveryRequest({ connections: { [buffer.key]: connectionWithoutSource }, credentialValues: buffer.credentialValues }, buffer.key) || !api?.discoverModels || testState?.status === "loading"} onClick={testConnection}>{testState?.status === "loading" ? "测试中…" : "测试连接方案"}</button>
      <button type="button" className="plain-action" onClick={cancelEditing}>取消</button>
      <button type="button" className="send" disabled={Boolean(missing)} onClick={save}>{previousKey ? "保存配置" : "创建连接"}</button>
    </footer>
  </div>;
}

// Merged pool: connection rows expand in place into their editor; collapsed
// and expanded states share one identical row head.
export function ConnectionSettings({ draft, setDraft, savedDraft, catalogs, api, activeSection, activeModelTab, onPersist, onDirtyChange }) {
  // Several rows can be open at once and every editor stays mounted, so a
  // folded row keeps its buffer until 取消 discards it. Each editor owns its
  // buffer, so one connection's unsaved edits never touch another. The
  // not-yet-created connection lives outside the draft until it is saved.
  const [openKeys, setOpenKeys] = useState([]);
  const [newOpen, setNewOpen] = useState(false);
  const [newExpanded, setNewExpanded] = useState(false);
  const [dirtyEditors, setDirtyEditors] = useState({});
  const dirtyByEditorRef = useRef({});

  const reportEditorDirty = useCallback((editorKey, value) => {
    dirtyByEditorRef.current[editorKey] = value;
    setDirtyEditors({ ...dirtyByEditorRef.current });
    onDirtyChange?.(Object.values(dirtyByEditorRef.current).some(Boolean));
  }, [onDirtyChange]);

  // The create form mounts closed so grid-template-rows can animate it open.
  useEffect(() => {
    if (!newOpen) { setNewExpanded(false); return; }
    const frame = requestAnimationFrame(() => setNewExpanded(true));
    return () => cancelAnimationFrame(frame);
  }, [newOpen]);

  const modelCount = (connectionKey) => Object.values(draft.models).filter((model) => model.connection === connectionKey).length;
  const rowMeta = (connectionKey) => {
    const connection = draft.connections[connectionKey];
    const protocols = Object.keys(connection.protocols || {});
    const protocolSummary = protocols.length === 1 ? protocolLabel(protocols[0]) : `${protocols.length} 个接口`;
    return `${protocolSummary} · ${modelCount(connectionKey)} 个模型`;
  };

  function addConnection() {
    const key = nextConnectionKey(draft.connections);
    let credentialIndex = 1;
    const used = new Set([...Object.values(draft.connections).map(connection => connection.credential?.env), ...Object.keys(draft.credentialValues)]);
    while (used.has(`LARA_PROVIDER_${credentialIndex}_API_KEY`)) credentialIndex += 1;
    setNewOpen({ key, connection: { ...emptyNativeConnection(key), provider: "", credential: { env: `LARA_PROVIDER_${credentialIndex}_API_KEY` }, credential_source: "missing", protocols: { openai_chat_completions: { base_url: "" } } }, models: {} });
  }

  function removeConnection(connectionKey) {
    setDraft((current) => {
      if (Object.values(current.models).some((model) => model.connection === connectionKey)) return current;
      const { [connectionKey]: _removed, ...connections } = current.connections;
      return { ...current, connections };
    });
  }

  function toggleConnection(connectionKey) {
    setOpenKeys((current) => current.includes(connectionKey) ? current.filter((key) => key !== connectionKey) : [...current, connectionKey]);
  }

  const connectionKeys = Object.keys(draft.connections);

  return <section hidden={activeSection !== "model-config" || activeModelTab !== "pool"} className="model-group-editor" aria-label="Native Pygent connections">
    <div className="model-group-heading">
      <div><strong>模型资源池</strong></div>
      <button className="route-add" type="button" disabled={newOpen} onClick={addConnection}><Plus aria-hidden="true" /> 添加连接</button>
    </div>
    {!connectionKeys.length && !newOpen && <p className="settings-inline-empty">还没有服务连接，请点击“添加连接”开始。</p>}
    <div className="model-route-list">
      {newOpen && <article className={`model-route-card connection-route-card pool-row${newExpanded ? " pool-row-open" : ""}`}>
        <div className="pool-row-head">
          <strong className="pool-new-title">添加模型连接</strong>
          {dirtyEditors[newOpen.key] && <span className="connection-editor-dirty">未保存</span>}
        </div>
        <div className="pool-editor-shell">
          <div className="pool-editor-inner">
            <ConnectionEditor
              key={`new-${newOpen.key}`}
              connectionKey={newOpen.key}
              previousKey={null}
              connection={newOpen.connection}
              models={newOpen.models}
              catalogs={catalogs}
              api={api}
              savedDraft={savedDraft}
              collapse={() => setNewOpen(false)}
              onPersist={onPersist}
              onDirtyChange={reportEditorDirty}
            />
          </div>
        </div>
      </article>}
      {connectionKeys.map((connectionKey) => {
        const connection = draft.connections[connectionKey];
        const referenced = modelCount(connectionKey) > 0;
        const open = openKeys.includes(connectionKey);
        return <article className={`model-route-card pool-row${open ? " pool-row-open" : ""}`} key={connectionKey}>
          <div className="pool-row-head">
            <button type="button" className="pool-row-main" aria-expanded={open} onClick={() => toggleConnection(connectionKey)}>
              <ChevronRight size={16} aria-hidden="true" />
              <Server size={16} aria-hidden="true" />
              <span className="connection-summary-text">
                <strong>{connectionKey}</strong>
                <small>{rowMeta(connectionKey)}</small>
              </span>
            </button>
            {dirtyEditors[connectionKey] && <span className="connection-editor-dirty">未保存</span>}
            <button className="route-remove" disabled={referenced} type="button" onClick={() => removeConnection(connectionKey)} aria-label={`Remove ${connectionKey}`} title={referenced ? "先删除此连接下的模型" : "删除连接"}><Trash2 aria-hidden="true" /></button>
          </div>
          <div className="pool-editor-shell">
            <div className="pool-editor-inner">
              <ConnectionEditor
                key={connectionKey}
                connectionKey={connectionKey}
                previousKey={connectionKey}
                connection={connection}
                models={Object.fromEntries(Object.entries(draft.models).filter(([, model]) => model.connection === connectionKey))}
                catalogs={catalogs}
                api={api}
                savedDraft={savedDraft}
                collapse={() => setOpenKeys((current) => current.filter((key) => key !== connectionKey))}
                onPersist={onPersist}
                onDirtyChange={reportEditorDirty}
              />
            </div>
          </div>
        </article>;
      })}
    </div>
  </section>;
}
