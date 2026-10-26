import React, { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronDown, ChevronRight, KeyRound, Pencil, Plus, RefreshCw, SlidersHorizontal, Trash2 } from "lucide-react";
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
export function ConnectionEditor({ connectionKey, previousKey, connection, models, catalogs, api, savedDraft, collapse, onPersist, onDirtyChange, open = false, onToggle = () => {}, onRemove = () => {}, removeDisabled = false, removeTitle = "" }) {
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
  const [expandedModel, setExpandedModel] = useState(null);
  const [modelDiscovered, setModelDiscovered] = useState({});
  const [modelDiscoveryState, setModelDiscoveryState] = useState({});
  // Read-only rows with per-row edit states: the key row and one endpoint row
  // at a time can switch from summary display into their inline editor.
  const [authEditing, setAuthEditing] = useState(false);
  const [editingEndpoint, setEditingEndpoint] = useState(null);
  const [editingName, setEditingName] = useState(false);
  const [providerMenuOpen, setProviderMenuOpen] = useState(false);
  const nameMenuRef = useRef(null);
  const metaRef = useRef(null);

  useEffect(() => {
    if (!providerMenuOpen) return;
    function onDocMouseDown(event) {
      if (nameMenuRef.current && !nameMenuRef.current.contains(event.target)) setProviderMenuOpen(false);
    }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [providerMenuOpen]);

  // The key row and endpoint rows fall back to their read-only summary as soon
  // as the pointer leaves the editable area; the buffer keeps pending changes.
  useEffect(() => {
    if (!authEditing && editingEndpoint === null) return;
    function onDocMouseDown(event) {
      if (metaRef.current && !metaRef.current.contains(event.target)) {
        setAuthEditing(false);
        setEditingEndpoint(null);
      }
    }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [authEditing, editingEndpoint]);

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

  function addModel(draft) {
    if (!draft.protocol || !draft.modelId) return;
    // A typed local name must stay unique across every model; duplicates fall
    // back to the next generated key instead of overwriting an entry.
    const scope = { ...savedDraft.models, ...buffer.models };
    const key = draft.key && !Object.hasOwn(scope, draft.key) ? draft.key : nextModelKey(scope);
    setBuffer((current) => ({ ...current, models: { ...current.models, [key]: { ...emptyNativeModel(current.key, draft.protocol), model_id: draft.modelId, capabilities: draft.capabilities } } }));
    // The saved row joins the list expanded, so its discover button and
    // capability form are immediately reachable like every other model row.
    setExpandedModel(key);
    setAddingModel(false);
  }

  function selectProvider(value) {
    if (!value) return;
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
    // Discovery results (fetched IDs and status text) render inside the
    // model body, so discovering from a folded row expands it first.
    setExpandedModel(modelKey);
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
    setAuthEditing(false);
    setEditingEndpoint(null);
    setEditingName(false);
    setProviderMenuOpen(false);
    setModelDiscovered({});
    setModelDiscoveryState({});
    setExpandedModel(null);
  }

  async function save() {
    if (missing) return;
    setSaveError("");
    try {
      await onPersist(previousKey, buffer, catalogs);
      // The create form closes on its first save because the saved row joins
      // the list below; existing editors stay open so models stay testable
      // and the saved view matches the editing view.
      if (!previousKey) collapse();
    } catch (error) {
      setSaveError(typeof error?.message === "string" && error.message ? error.message : "保存失败，请稍后再试。");
    }
  }

  function commitRename(nextKey) {
    setEditingName(false);
    setProviderMenuOpen(false);
    if (nextKey !== buffer.key) renameConnectionKey(nextKey);
  }

  // The head's 添加接口 expands the row and opens the new endpoint in edit state.
  function addEndpointRow() {
    const protocol = addableProtocols[0];
    if (!protocol) return;
    setBuffer((current) => {
      if (current.connection.protocols?.[protocol]) return current;
      const baseUrl = catalogs?.providers?.[current.connection.provider]?.protocols?.[protocol]?.base_url || "";
      return { ...current, connection: { ...current.connection, protocols: { ...current.connection.protocols, [protocol]: { base_url: baseUrl } } } };
    });
    setEditingEndpoint(protocol);
    if (!open) onToggle();
  }

  function protocolSelect(protocol) {
    return <label>
      <span>协议名称</span>
      <select value={protocol} onChange={(event) => {
        setEditingEndpoint(event.target.value);
        setBuffer((current) => {
          const next = changeConnectionProtocol({ connections: { [current.key]: current.connection }, models: current.models, modelGroups: {}, credentialValues: current.credentialValues }, current.key, protocol, event.target.value);
          return { ...current, connection: next.connections[current.key], models: next.models };
        });
      }}>
        {protocolChoices.map((key) => <option key={key} value={key} disabled={key !== protocol && Boolean(buffer.connection.protocols?.[key])}>{protocolLabel(key)}</option>)}
      </select>
    </label>;
  }

  // The row head is part of the editor because the name, badges, save and
  // discard state all live in the buffer; collapsed and expanded states share
  // this exact head and collapsed rows show nothing else. The name is plain
  // text (double-click renames custom providers) and the caret opens the
  // provider switch menu.
  const headName = isCustomProvider || !provider ? buffer.key : (catalogs?.providers?.[provider]?.display_name || provider);
  const providerMenuItems = [
    ...Object.entries(catalogs?.providers || {}).map(([key, item]) => ({ value: key, label: item.display_name || key, group: "Pygent 内置" })),
    ...(provider && providerSelection !== "__custom__" && !Object.hasOwn(catalogs?.providers || {}, provider) ? [{ value: provider, label: provider, group: "当前" }] : []),
    { value: "__custom__", label: "自定义供应商", group: "" },
  ];
  return <>
    <div className="pool-row-head connection-row-head">
      <button type="button" className="pool-row-main" aria-expanded={open} onClick={onToggle}>
        <ChevronRight size={16} aria-hidden="true" />
      </button>
      <div className="head-name-wrap" ref={nameMenuRef}>
        {editingName
          ? <div className="head-name-input-wrap">
              <input className="head-name-input" defaultValue={buffer.key} key={buffer.key} autoFocus aria-label="供应商名称"
                onKeyDown={(event) => { if (event.key === "Enter") commitRename(event.currentTarget.value); if (event.key === "Escape") { setEditingName(false); setProviderMenuOpen(false); } }}
                onBlur={(event) => { if (nameMenuRef.current?.contains(event.relatedTarget)) return; commitRename(event.currentTarget.value); }} />
              {/* preventDefault keeps the input focused, so opening the menu does not end the edit */}
              <button type="button" className="head-caret" aria-label="选择服务商" title="选择服务商" onMouseDown={(event) => event.preventDefault()} onClick={() => setProviderMenuOpen((current) => !current)}><ChevronDown size={14} aria-hidden="true" /></button>
            </div>
          : !provider
            ? <select className="head-provider-select" value="" aria-label="选择服务商" title="选择服务商" onChange={(event) => { setProviderMenuOpen(false); selectProvider(event.target.value); }}>
                <option value="" disabled>请选择服务商</option>
                <optgroup label="Pygent 内置">{Object.entries(catalogs?.providers || {}).map(([key, item]) => <option key={key} value={key}>{item.display_name || key}</option>)}</optgroup>
                <option value="__custom__">自定义供应商…</option>
              </select>
            : <button type="button" className="head-name" onDoubleClick={isCustomProvider ? () => setEditingName(true) : undefined} title={isCustomProvider ? "双击重命名" : undefined}>{headName}</button>}
        {providerMenuOpen && <div className="head-provider-menu" role="menu">
          {providerMenuItems.map((item, index) => (
            <React.Fragment key={item.value}>
              {(item.group && (index === 0 || providerMenuItems[index - 1].group !== item.group)) && <div className="menu-group">{item.group}</div>}
              <button type="button" role="menuitem" className={item.value === providerSelection ? "selected" : ""} onClick={() => { setProviderMenuOpen(false); setEditingName(false); selectProvider(item.value); }}>{item.label}</button>
            </React.Fragment>
          ))}
        </div>}
      </div>
      <span className="head-count-badge">{protocols.length} 个接口</span>
      <span className="head-count-badge">{Object.keys(buffer.models).length} 个模型</span>
      <button type="button" className="head-add-endpoint" disabled={!addableProtocols.length} title={addableProtocols.length ? "添加接口" : "所有协议均已添加"} onClick={addEndpointRow}><Plus size={14} aria-hidden="true" /> 添加接口</button>
      <button type="button" className="send" disabled={Boolean(missing)} title={missing || "保存当前连接与模型"} onClick={save}>保存</button>
      {dirty && previousKey && <button type="button" className="link-action" onClick={discardEdits}>放弃</button>}
      <button className="route-remove" disabled={removeDisabled} type="button" onClick={onRemove} aria-label={`Remove ${connectionKey}`} title={removeTitle || "删除连接"}><Trash2 aria-hidden="true" /></button>
    </div>
    <div className="pool-editor-shell">
      <div className="pool-editor-inner">
        <div className="connection-route-card connection-editor">
          {missing && <div className="connection-missing" role="status">{missing}</div>}
          {saveError && <div className="connection-missing" role="status">{saveError}</div>}

          <div className="connection-meta" ref={metaRef}>
            <div className="connection-key-row" onDoubleClick={authEditing ? undefined : () => setAuthEditing(true)}>
              <KeyRound size={14} aria-hidden="true" />
              <span className="auth-label">API Key</span>
              {authEditing ? (authMode === "api-key" ? <>
                <input disabled={!credentialEnv} type="password" autoComplete="off" aria-label="API Key" placeholder={savedKey ? "留空保留已有密钥" : "填写 API Key"} value={buffer.credentialValues[credentialEnv] || ""} onChange={(event) => setBuffer((current) => ({ ...current, credentialValues: { ...current.credentialValues, [credentialEnv]: event.target.value } }))} autoFocus />
                <button type="button" className="link-action" onClick={() => updateConnection("credential", { none: true })}>切换为无需认证</button>
              </> : <>
                <span className="key-env">此连接无需密钥</span>
                <button type="button" className="link-action" onClick={() => updateConnection("credential", { env: credentialEnv || `${buffer.key.toUpperCase().replace(/[^A-Z0-9]+/g, "_")}_API_KEY` })}>改用 API Key</button>
              </>)
                : (authMode === "api-key" ? <>
                  <span className={`key-state ${savedKey ? "ok" : "required"}`}>{savedKey ? "已配置" : "必填"}</span>
                  <span className="key-env">{credentialEnv}</span>
                </> : <span className="key-env">无需认证</span>)}
              <button type="button" className="row-edit-action" aria-label={authEditing ? "完成凭据编辑" : "编辑凭据"} title={authEditing ? "完成" : "编辑"} onClick={() => setAuthEditing((current) => !current)}>
                {authEditing ? <Check size={14} aria-hidden="true" /> : <Pencil size={14} aria-hidden="true" />}
              </button>
            </div>
            <div className="protocol-interface-list">
              {protocols.map(([protocol, endpoint]) => editingEndpoint === protocol ? (
                <div className="protocol-interface-row" key={protocol}>
                  {protocolSelect(protocol)}
                  <label><span>Base URL</span><input placeholder="https://api.example.com/v1" value={endpoint?.base_url || ""} onChange={(event) => setBuffer((current) => ({ ...current, connection: { ...current.connection, protocols: { ...current.connection.protocols, [protocol]: { base_url: event.target.value } } } }))} /></label>
                  <button
                    className="route-remove"
                    disabled={protocols.length === 1 || Object.values(buffer.models).some((model) => model.protocol === protocol)}
                    type="button"
                    onClick={() => {
                      setEditingEndpoint(null);
                      setBuffer((current) => {
                        const { [protocol]: _removed, ...nextProtocols } = current.connection.protocols || {};
                        return { ...current, connection: { ...current.connection, protocols: nextProtocols } };
                      });
                    }}
                    aria-label={`Remove ${protocol}`}
                    title={protocols.length === 1 ? "至少保留一个接口" : "删除接口"}
                  ><Trash2 aria-hidden="true" /></button>
                  <button type="button" className="row-edit-action" aria-label="完成接口编辑" title="完成" onClick={() => setEditingEndpoint(null)}><Check size={14} aria-hidden="true" /></button>
                </div>
              ) : (
                <div className="protocol-summary-row" key={protocol} onDoubleClick={() => setEditingEndpoint(protocol)}>
                  <span className="protocol-summary-text">{protocolLabel(protocol)} · {endpoint?.base_url}</span>
                  <button type="button" className="row-edit-action" aria-label={`编辑接口 ${protocolLabel(protocol)}`} title="编辑" onClick={() => setEditingEndpoint(protocol)}><Pencil size={14} aria-hidden="true" /></button>
                </div>
              ))}
            </div>
          </div>

    <div className="connection-models">
      <div className="connection-section-heading">
        <strong>模型</strong>
        <button type="button" className="plain-action connection-add-model" disabled={addingModel || !protocols.length} onClick={() => setAddingModel(true)}><Plus size={14} aria-hidden="true" /> 添加模型</button>
      </div>
      {addingModel && <AddModelForm connection={buffer.connection} credentialValue={buffer.credentialValues[credentialEnv] || ""} catalogs={catalogs} api={api} defaultModelKey={nextModelKey({ ...savedDraft.models, ...buffer.models })} onAdd={addModel} onCancel={() => setAddingModel(false)} />}
      {Object.keys(buffer.models).length > 0 && <div className="model-route-list connection-model-list">
        {Object.entries(buffer.models).map(([modelKey, model]) => {
          const protocolSelectable = protocols.length > 1 || (Boolean(model.protocol) && !protocols.includes(model.protocol));
          const open = expandedModel === modelKey;
          return <article className={`model-route-card pool-row${open ? " pool-row-open" : ""}`} key={modelKey}>
            <div className="pool-row-head model-row-head">
              <button type="button" className="pool-row-main" aria-expanded={open} title={open ? "收起能力配置" : "展开能力配置"} onClick={() => setExpandedModel(open ? null : modelKey)}><ChevronRight size={16} aria-hidden="true" /></button>
              <div className="head-field field-id">
                <div className="model-id-control">
                  <input list={`models-${modelKey}`} placeholder="填写模型 ID" value={model.model_id || ""} onChange={(event) => updateModelId(modelKey, event.target.value)} aria-label="服务商模型 ID" title="服务商模型 ID（服务商文档或“发现模型”返回的真实 ID）" />
                  <button className="discover-action" disabled={!api?.discoverModels || modelDiscoveryState[modelKey]?.loading} type="button" onClick={() => discoverModelIds(modelKey)} aria-label="通过连接发现模型 ID" title="通过连接发现模型 ID"><RefreshCw size={14} aria-hidden="true" /></button>
                </div>
              </div>
              <datalist id={`models-${modelKey}`}>{(modelDiscovered[modelKey] || []).map((item) => <option key={item.id} value={item.id} />)}</datalist>
              <div className="head-field field-name">
                <span className="head-tag">名称</span>
                <input defaultValue={modelKey} key={modelKey} aria-label="本地名称" title="本地名称（仅本地引用，不会发送给服务商）" onBlur={(event) => renameModelKey(modelKey, event.target.value)} />
              </div>
              {protocolSelectable && <div className="head-field field-protocol">
                <span className="head-tag">协议</span>
                <select value={model.protocol || ""} onChange={(event) => updateModel(modelKey, "protocol", event.target.value)} aria-label="API 协议" title="只能选择当前连接提供的协议">{!protocols.includes(model.protocol) && <option value={model.protocol}>{protocolLabel(model.protocol)}</option>}{protocols.map(([key]) => <option key={key} value={key}>{protocolLabel(key)}</option>)}</select>
              </div>}
              <div className="model-head-actions">
                <button
                  className="route-remove"
                  type="button"
                  onClick={() => removeModel(modelKey)}
                  aria-label={`Remove ${modelKey}`}
                  title="删除模型"
                ><Trash2 aria-hidden="true" /></button>
              </div>
            </div>
            <div className="pool-editor-shell">
              <div className="pool-editor-inner">
                <div className="model-config-block">
                  {modelDiscoveryState[modelKey]?.message && <small className="route-secret" role="status">{modelDiscoveryState[modelKey].message}</small>}
                  <ModelCapabilitiesEditor value={model.capabilities} catalogs={catalogs} provider={provider} protocol={model.protocol || ""} onChange={(value) => updateModel(modelKey, "capabilities", value)} />
                </div>
              </div>
            </div>
          </article>;
        })}
      </div>}
      {!Object.keys(buffer.models).length && !addingModel && <p className="settings-inline-empty">此连接下还没有模型，点击“添加模型”。</p>}
    </div>

    <details className="connection-advanced settings-detail">
      <summary><SlidersHorizontal size={14} aria-hidden="true" /> 高级配置 <span className="advanced-hint">认证 · 代理 · TLS <ChevronRight size={14} className="chev" aria-hidden="true" /></span></summary>
      <div className="connection-test-row">
        <button type="button" className="plain-action" disabled={!modelDiscoveryRequest({ connections: { [buffer.key]: connectionWithoutSource }, credentialValues: buffer.credentialValues }, buffer.key) || !api?.discoverModels || testState?.status === "loading"} onClick={testConnection}>{testState?.status === "loading" ? "测试中…" : "测试连接方案"}</button>
        {testState && testState.status !== "loading" && <span className={`connection-test-status ${testState.status}`} role="status">{testState.status === "ready" ? `连接可用，发现 ${testState.count} 个模型` : TEST_STATUS_TEXT[testState.status]}</span>}
      </div>
      <div className="model-route-grid">
        {authMode === "api-key" && <label><span>凭据名称</span><input value={credentialEnv} onChange={(event) => updateConnection("credential", event.target.value ? { env: event.target.value } : { none: true })} /><small>配置只保存名称，不保存密钥。</small></label>}
        <label><span>代理（可选）</span><input placeholder="http://127.0.0.1:7890" value={buffer.connection.proxy || ""} onChange={(event) => updateConnection("proxy", event.target.value)} /></label>
        <label><span>TLS 证书验证</span><select value={buffer.connection.verify_ssl === false ? "off" : "on"} onChange={(event) => updateConnection("verify_ssl", event.target.value === "on")}><option value="on">开启（推荐）</option><option value="off">关闭</option></select></label>
      </div>
    </details>
        </div>
      </div>
    </div>
  </>;
}

// Merged pool: connection rows expand in place into their editor; collapsed
// and expanded states share one identical row head.
export function ConnectionSettings({ draft, setDraft, savedDraft, catalogs, api, activeSection, activeModelTab, onPersist, onDirtyChange }) {
  // Several rows can be open at once and every editor stays mounted, so a
  // folded row keeps its buffer until 放弃 discards it. Each editor owns its
  // buffer, row head and save button, so one connection's unsaved edits never
  // touch another. The not-yet-created connection lives outside the draft
  // until it is saved.
  const [openKeys, setOpenKeys] = useState([]);
  const [newOpen, setNewOpen] = useState(false);
  const [newExpanded, setNewExpanded] = useState(false);
  const dirtyByEditorRef = useRef({});

  const reportEditorDirty = useCallback((editorKey, value) => {
    dirtyByEditorRef.current[editorKey] = value;
    onDirtyChange?.(Object.values(dirtyByEditorRef.current).some(Boolean));
  }, [onDirtyChange]);

  // The create form mounts closed so grid-template-rows can animate it open.
  useEffect(() => {
    if (!newOpen) { setNewExpanded(false); return; }
    const frame = requestAnimationFrame(() => setNewExpanded(true));
    return () => cancelAnimationFrame(frame);
  }, [newOpen]);

  const modelCount = (connectionKey) => Object.values(draft.models).filter((model) => model.connection === connectionKey).length;

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
          open={newExpanded}
          onToggle={() => {}}
          onRemove={() => setNewOpen(false)}
          removeTitle="放弃创建"
        />
      </article>}
      {connectionKeys.map((connectionKey) => {
        const connection = draft.connections[connectionKey];
        const referenced = modelCount(connectionKey) > 0;
        const open = openKeys.includes(connectionKey);
        return <article className={`model-route-card pool-row${open ? " pool-row-open" : ""}`} key={connectionKey}>
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
            open={open}
            onToggle={() => toggleConnection(connectionKey)}
            onRemove={() => removeConnection(connectionKey)}
            removeDisabled={referenced}
            removeTitle={referenced ? "先删除此连接下的模型" : "删除连接"}
          />
        </article>;
      })}
    </div>
  </section>;
}
