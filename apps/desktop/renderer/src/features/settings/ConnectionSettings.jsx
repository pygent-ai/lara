import React, { useCallback, useEffect, useRef, useState } from "react";
import { ChevronRight, Plus, Trash2 } from "lucide-react";
import { ModelPicker } from "./ModelPicker.jsx";
import { ModelCapabilitiesEditor } from "./ModelCapabilitiesEditor.jsx";
import { SectionHead } from "./SectionHead.jsx";
import { ConfirmDelete, errorText } from "./ConfirmDelete.jsx";
import {
  capabilitySummary,
  catalogCapabilities,
  commitConnection,
  commitConnectionDelete,
  commitModelDelete,
  connectionBuffer,
  connectionDirty,
  connectionError,
  credentialEnvFor,
  defaultProtocolFor,
  deletionImpact,
  modelDiscoveryRequest,
  newConnectionBuffer,
  newModel,
  originalModelKey,
  protocolChoices,
  protocolLabel,
  removeBufferModel,
  renameBufferModel,
  switchBufferProvider,
  uniqueKey,
} from "./settingsModel.js";

function providerName(provider, catalogs) {
  return catalogs?.providers?.[provider]?.display_name || provider;
}

function credentialChip(connection, credentialValue) {
  if (connection.credential?.none) return ["none", "无需认证"];
  if (credentialValue?.trim()) return ["pending", "API Key 待保存"];
  if (connection.credential_source && connection.credential_source !== "missing") return ["ok", "已配置"];
  return ["missing", "缺少 API Key"];
}

// One model inside the connection editor: a one-line summary that expands
// into the local name, protocol and capability editor.
function ModelRow({ modelKey, model, protocols, catalogs, provider, open, onToggle, onRename, onChange, onRemove, saved }) {
  const multiProtocol = protocols.length > 1 || !protocols.includes(model.protocol);
  return <article className={`model-row${open ? " open" : ""}`}>
    <div className="model-row-head">
      <button type="button" className="model-row-toggle" aria-expanded={open} onClick={onToggle}>
        <ChevronRight size={16} aria-hidden="true" />
        <span className="model-row-copy">
          <strong>{model.model_id || "未填写模型 ID"}{modelKey !== model.model_id && <small className="model-key"> {modelKey}</small>}{!saved && <span className="unsaved-chip">未保存</span>}</strong>
          <small>{multiProtocol ? `${protocolLabel(model.protocol)} · ` : ""}{capabilitySummary(model.capabilities)}</small>
        </span>
      </button>
      <button type="button" className="icon-action" title="删除模型" aria-label={`删除模型 ${modelKey}`} onClick={onRemove}><Trash2 size={14} aria-hidden="true" /></button>
    </div>
    {open && <div className="model-row-body">
      <div className="field-grid">
        <label>
          <span>模型 ID</span>
          <input value={model.model_id} onChange={(event) => onChange({ model_id: event.target.value, ...(catalogCapabilities(catalogs, provider, model.protocol, event.target.value) ? { capabilities: catalogCapabilities(catalogs, provider, model.protocol, event.target.value) } : {}) })} aria-label="模型 ID" />
          <small>发送给服务商的真实模型名。</small>
        </label>
        <label>
          <span>本地名称</span>
          <input defaultValue={modelKey} key={modelKey} onBlur={(event) => onRename(event.target.value)} aria-label="本地名称" />
          <small>仅在本地引用（模型组、对话选择），重命名会同步已有对话。</small>
        </label>
        {multiProtocol && <label>
          <span>API 协议</span>
          <select value={model.protocol} onChange={(event) => onChange({ protocol: event.target.value })} aria-label="API 协议">
            {!protocols.includes(model.protocol) && <option value={model.protocol}>{protocolLabel(model.protocol)}（接口已移除）</option>}
            {protocols.map((key) => <option key={key} value={key}>{protocolLabel(key)}</option>)}
          </select>
        </label>}
      </div>
      <ModelCapabilitiesEditor value={model.capabilities} catalogs={catalogs} provider={provider} protocol={model.protocol} onChange={(capabilities) => onChange({ capabilities })} />
    </div>}
  </article>;
}

// Editor for one connection and its models. Everything edits a local buffer
// and one 保存 commits it; deleting a saved model asks first and commits at
// once. `previousKey` is null for a connection being created.
export function ConnectionEditor({ previousKey, initialBuffer, savedDraft, catalogs, usage, api, persist, reportDirty, onCreated, onCancelCreate, autoPick = null }) {
  const [buffer, setBuffer] = useState(initialBuffer);
  const [picking, setPicking] = useState(Boolean(autoPick));
  useEffect(() => { autoPick?.(); }, []);
  const [openModel, setOpenModel] = useState(null);
  const [confirmModel, setConfirmModel] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [test, setTest] = useState(null);
  const editorId = previousKey || "__new__";

  const dirty = connectionDirty(buffer, savedDraft, previousKey);
  useEffect(() => { reportDirty(editorId, dirty); return () => reportDirty(editorId, false); }, [dirty, editorId, reportDirty]);

  const connection = buffer.connection;
  const protocols = Object.keys(connection.protocols || {});
  const chosen = Boolean(connection.provider);
  const custom = chosen && !catalogs?.providers?.[connection.provider];
  const validation = connectionError(buffer, savedDraft, previousKey);
  const savedModels = savedDraft.models;
  const [chipKind, chipText] = credentialChip(connection, buffer.credentialValue);
  const addableProtocols = protocolChoices(custom ? "" : connection.provider, catalogs).filter((protocol) => !connection.protocols[protocol]);

  function update(patch) {
    setError("");
    setBuffer((current) => ({ ...current, ...patch }));
  }

  function updateConnection(patch) {
    update({ connection: { ...connection, ...patch } });
  }

  function chooseProvider(value) {
    if (!value) return;
    setError("");
    setBuffer((current) => {
      // A new connection takes the provider's key as its name until renamed.
      const key = previousKey ? current.key : uniqueKey(value === "__custom__" ? "custom" : value, savedDraft.connections);
      return switchBufferProvider({ ...current, key }, value === "__custom__" ? key : value, catalogs);
    });
  }

  function renameConnection(value) {
    const key = value.trim();
    if (!key || key === buffer.key) return;
    update({ key, connection: custom ? { ...connection, provider: key } : connection });
  }

  function addModels(entries) {
    setBuffer((current) => {
      const models = { ...current.models };
      for (const { modelId, protocol } of entries) {
        const key = uniqueKey(modelId, { ...savedModels, ...models });
        models[key] = newModel(current.key, current.connection, modelId, protocol, catalogs);
      }
      return { ...current, models };
    });
    setPicking(false);
  }

  function isSavedModel(modelKey) {
    return Object.hasOwn(savedModels, originalModelKey(buffer.renames, modelKey));
  }

  async function removeModel(modelKey) {
    if (!isSavedModel(modelKey)) { update(removeBufferModel(buffer, modelKey)); return; }
    setBusy(true);
    try {
      await persist(commitModelDelete(savedDraft, originalModelKey(buffer.renames, modelKey)));
      setBuffer((current) => removeBufferModel(current, modelKey));
      setConfirmModel(null);
    } catch (deleteError) {
      setError(errorText(deleteError));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (validation) return;
    setError("");
    setBusy(true);
    try {
      const renames = Object.fromEntries(Object.entries(buffer.renames).filter(([original]) => Object.hasOwn(savedModels, original)));
      const rebased = await persist(commitConnection(savedDraft, previousKey, buffer), { modelKeyRenames: Object.keys(renames).length ? renames : undefined });
      const key = buffer.key.trim();
      if (previousKey) setBuffer(connectionBuffer(key, rebased));
      else onCreated(key, Object.keys(buffer.models).length === 0);
    } catch (saveError) {
      setError(errorText(saveError));
    } finally {
      setBusy(false);
    }
  }

  async function testConnection() {
    const request = modelDiscoveryRequest(connection, defaultProtocolFor(connection, catalogs), buffer.credentialValue);
    if (!request || !api?.discoverModels) return;
    setTest({ status: "loading" });
    try {
      const response = await api.discoverModels(request);
      const count = Array.isArray(response?.models) ? response.models.length : 0;
      setTest({ status: "ok", text: count ? `连接可用，服务商返回 ${count} 个模型` : "连接可用，但服务商没有返回模型列表" });
    } catch {
      setTest({ status: "error", text: "连接失败，请检查地址与密钥" });
    }
  }

  const providerOptions = Object.entries(catalogs?.providers || {});
  const modelEntries = Object.entries(buffer.models);

  return <div className="connection-editor">
    {error && !confirmModel && <p className="editor-error" role="alert">{error}</p>}
    <div className="connection-fields">
      <div className="field-grid">
        <label>
          <span>服务商</span>
          <select value={chosen ? (custom ? "__custom__" : connection.provider) : ""} onChange={(event) => chooseProvider(event.target.value)} aria-label="服务商">
            {!chosen && <option value="" disabled>请选择服务商</option>}
            {providerOptions.map(([key, item]) => <option key={key} value={key}>{item.display_name || key}</option>)}
            <option value="__custom__">自定义（OpenAI 兼容等）</option>
          </select>
        </label>
        {chosen && <label>
          <span>连接名称</span>
          <input defaultValue={buffer.key} key={buffer.key} onBlur={(event) => renameConnection(event.target.value)} aria-label="连接名称" />
          <small>本地标识，模型通过它引用连接。</small>
        </label>}
      </div>
      {chosen && <div className="credential-row">
        <label className="credential-field">
          <span>API Key <span className={`credential-chip ${chipKind}`}>{chipText}</span></span>
          <input type="password" autoComplete="off" disabled={Boolean(connection.credential?.none)} placeholder={connection.credential?.none ? "此服务无需密钥" : chipKind === "ok" ? "留空保留已有密钥" : "粘贴 API Key"} value={buffer.credentialValue} onChange={(event) => update({ credentialValue: event.target.value })} aria-label="API Key" />
        </label>
        <label className="credential-none">
          <input type="checkbox" checked={Boolean(connection.credential?.none)} onChange={(event) => updateConnection({ credential: event.target.checked ? { none: true } : { env: credentialEnvFor(buffer.key) } })} />
          <span>无需认证</span>
        </label>
      </div>}
      {chosen && <div className="endpoint-list" aria-label="接口">
        <span className="field-label">接口 <small>协议与地址</small></span>
        {protocols.map((protocol) => {
          const referenced = modelEntries.some(([, model]) => model.protocol === protocol);
          return <div className="endpoint-row" key={protocol}>
            <select value={protocol} aria-label="API 协议" onChange={(event) => {
              const next = event.target.value;
              const nextProtocols = Object.fromEntries(Object.entries(connection.protocols).map(([key, value]) => [key === protocol ? next : key, value]));
              update({ connection: { ...connection, protocols: nextProtocols }, models: Object.fromEntries(modelEntries.map(([key, model]) => [key, model.protocol === protocol ? { ...model, protocol: next } : model])) });
            }}>
              {protocolChoices(custom ? "" : connection.provider, catalogs).map((key) => <option key={key} value={key} disabled={key !== protocol && Boolean(connection.protocols[key])}>{protocolLabel(key)}</option>)}
            </select>
            <input placeholder="https://api.example.com/v1" value={connection.protocols[protocol]?.base_url || ""} aria-label={`${protocolLabel(protocol)} Base URL`} onChange={(event) => updateConnection({ protocols: { ...connection.protocols, [protocol]: { base_url: event.target.value } } })} />
            <button type="button" className="icon-action" disabled={protocols.length === 1 || referenced} title={protocols.length === 1 ? "至少保留一个接口" : referenced ? "有模型使用此接口" : "删除接口"} aria-label={`删除接口 ${protocolLabel(protocol)}`} onClick={() => { const { [protocol]: _removed, ...rest } = connection.protocols; updateConnection({ protocols: rest }); }}><Trash2 size={14} aria-hidden="true" /></button>
          </div>;
        })}
        {addableProtocols.length > 0 && <button type="button" className="link-action" onClick={() => updateConnection({ protocols: { ...connection.protocols, [addableProtocols[0]]: { base_url: catalogs?.providers?.[connection.provider]?.protocols?.[addableProtocols[0]]?.base_url || "" } } })}><Plus size={12} aria-hidden="true" /> 添加接口</button>}
      </div>}
    </div>

    {chosen && <div className="connection-models">
      <div className="connection-models-head">
        <span className="field-label">模型 <small>{modelEntries.length ? `${modelEntries.length} 个` : "还没有模型"}</small></span>
        <button type="button" className="plain-action" disabled={picking || !protocols.length} onClick={() => setPicking(true)}><Plus size={14} aria-hidden="true" /> 添加模型</button>
      </div>
      {picking && <ModelPicker connection={connection} credentialValue={buffer.credentialValue} existingIds={modelEntries.map(([, model]) => model.model_id)} api={api} onAdd={addModels} onCancel={() => setPicking(false)} />}
      {modelEntries.map(([modelKey, model]) => <React.Fragment key={modelKey}>
        <ModelRow
          modelKey={modelKey}
          model={model}
          protocols={protocols}
          catalogs={catalogs}
          provider={connection.provider}
          open={openModel === modelKey}
          saved={isSavedModel(modelKey)}
          onToggle={() => setOpenModel((current) => (current === modelKey ? null : modelKey))}
          onRename={(value) => { const next = renameBufferModel(buffer, modelKey, value); if (next !== buffer) { update(next); if (openModel === modelKey) setOpenModel(value.trim()); } }}
          onChange={(patch) => update({ models: { ...buffer.models, [modelKey]: { ...model, ...patch } } })}
          onRemove={() => (isSavedModel(modelKey) ? setConfirmModel(modelKey) : removeModel(modelKey))}
        />
        {confirmModel === modelKey && <ConfirmDelete title={`删除模型 ${model.model_id || modelKey}？`} impact={deletionImpact("model", modelKey, savedDraft, usage)} busy={busy} error={error} onConfirm={() => removeModel(modelKey)} onCancel={() => { setConfirmModel(null); setError(""); }} />}
      </React.Fragment>)}
      {!modelEntries.length && !picking && <p className="field-note">点击「添加模型」从服务商获取模型列表并勾选。</p>}
    </div>}

    {chosen && <details className="connection-advanced">
      <summary>高级 <span>凭据名称 · 代理 · TLS · 测试连接</span></summary>
      <div className="field-grid">
        {!connection.credential?.none && <label>
          <span>凭据名称</span>
          <input value={connection.credential?.env || ""} onChange={(event) => updateConnection({ credential: { env: event.target.value } })} aria-label="凭据名称" />
          <small>密钥保存在本机凭据文件中，配置只记录这个名称。</small>
        </label>}
        <label>
          <span>代理（可选）</span>
          <input placeholder="http://127.0.0.1:7890" value={connection.proxy || ""} onChange={(event) => updateConnection({ proxy: event.target.value || undefined })} />
        </label>
        <label>
          <span>TLS 证书验证</span>
          <select value={connection.verify_ssl === false ? "off" : "on"} onChange={(event) => updateConnection({ verify_ssl: event.target.value === "on" })}>
            <option value="on">开启（推荐）</option>
            <option value="off">关闭</option>
          </select>
        </label>
      </div>
      <div className="connection-test">
        <button type="button" className="plain-action" disabled={!api?.discoverModels || test?.status === "loading" || Boolean(connectionError({ ...buffer, models: {} }, savedDraft, previousKey))} onClick={testConnection}>{test?.status === "loading" ? "测试中…" : "测试连接"}</button>
        {test && test.status !== "loading" && <span className={`connection-test-result ${test.status}`} role="status">{test.text}</span>}
      </div>
    </details>}

    <div className="editor-footer">
      {validation && chosen && <span className="editor-hint">{validation}</span>}
      {previousKey
        ? dirty && <button type="button" className="plain-action" onClick={() => { setBuffer(connectionBuffer(previousKey, savedDraft)); setPicking(false); setError(""); }}>放弃修改</button>
        : <button type="button" className="plain-action" onClick={onCancelCreate}>取消</button>}
      <button type="button" className="primary-action" disabled={!dirty || Boolean(validation) || busy} title={validation || undefined} onClick={save} aria-label={previousKey ? `保存连接 ${previousKey}` : "保存新连接"}>{busy ? "保存中…" : previousKey ? "保存" : "创建连接"}</button>
    </div>
  </div>;
}

// Connections and their models. Each saved connection is a row that expands
// into its editor; a new connection opens at the top and joins the list once
// created. Deleting a connection removes its models and asks first.
export function ConnectionSettings({ savedDraft, catalogs, usage, api, persist, reportDirty }) {
  const [openKeys, setOpenKeys] = useState([]);
  const [creating, setCreating] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // The editor of a freshly created connection opens its model picker so the
  // next step (adding models) is on screen without another click.
  const autoPickRef = useRef(null);
  // Row heads show which collapsed editors still hold unsaved edits.
  const [dirtyKeys, setDirtyKeys] = useState({});
  const reportEditorDirty = useCallback((id, value) => {
    setDirtyKeys((current) => (current[id] === value ? current : { ...current, [id]: value }));
    reportDirty(id, value);
  }, [reportDirty]);

  const toggle = useCallback((key) => setOpenKeys((current) => (current.includes(key) ? current.filter((item) => item !== key) : [...current, key])), []);

  async function removeConnection(key) {
    setBusy(true);
    try {
      await persist(commitConnectionDelete(savedDraft, key));
      setConfirm(null);
      setOpenKeys((current) => current.filter((item) => item !== key));
    } catch (deleteError) {
      setError(errorText(deleteError));
    } finally {
      setBusy(false);
    }
  }

  const keys = Object.keys(savedDraft.connections);

  return <section className="settings-card" aria-label="连接与模型">
    <SectionHead title="连接与模型" description="每个连接对应一个服务商和它的 API Key；在连接下添加要使用的模型，再到「模型组」里组织主模型与回退顺序。">
      <button type="button" className="plain-action" disabled={Boolean(creating)} onClick={() => setCreating(newConnectionBuffer("", savedDraft, catalogs))}><Plus size={14} aria-hidden="true" /> 添加连接</button>
    </SectionHead>
    {creating && <article className="connection-row open creating">
      <div className="connection-row-head"><strong>新连接</strong></div>
      <ConnectionEditor
        key="new"
        previousKey={null}
        initialBuffer={creating}
        savedDraft={savedDraft}
        catalogs={catalogs}
        usage={usage}
        api={api}
        persist={persist}
        reportDirty={reportEditorDirty}
        onCreated={(key, empty) => {
          setCreating(null);
          setOpenKeys((current) => [...current, key]);
          if (empty) autoPickRef.current = key;
          requestAnimationFrame(() => document.querySelector(`[data-connection="${CSS.escape(key)}"]`)?.scrollIntoView({ block: "start", behavior: "smooth" }));
        }}
        onCancelCreate={() => setCreating(null)}
      />
    </article>}
    {!keys.length && !creating && <p className="settings-empty">还没有连接。点击「添加连接」，选择服务商并填写 API Key。</p>}
    {keys.map((key) => {
      const connection = savedDraft.connections[key];
      const models = Object.values(savedDraft.models).filter((model) => model.connection === key);
      const open = openKeys.includes(key);
      const [chipKind, chipText] = credentialChip(connection, "");
      const custom = !catalogs?.providers?.[connection.provider];
      return <article className={`connection-row${open ? " open" : ""}`} key={key} data-connection={key}>
        <div className="connection-row-head">
          <button type="button" className="connection-row-toggle" aria-expanded={open} onClick={() => toggle(key)}>
            <ChevronRight size={16} aria-hidden="true" />
            <strong>{custom ? key : providerName(connection.provider, catalogs)}{!custom && key !== connection.provider && <small className="model-key"> {key}</small>}</strong>
            <span className={`credential-chip ${chipKind}`}>{chipText}</span>
            <span className="count-chip">{models.length ? `${models.length} 个模型` : "没有模型"}</span>
            {dirtyKeys[key] && <span className="unsaved-chip">未保存</span>}
          </button>
          <button type="button" className="icon-action" title="删除连接及其模型" aria-label={`删除连接 ${key}`} onClick={() => setConfirm(key)}><Trash2 size={14} aria-hidden="true" /></button>
        </div>
        {confirm === key && <ConfirmDelete title={`删除连接 ${key} 及其 ${models.length} 个模型？`} impact={deletionImpact("connection", key, savedDraft, usage)} busy={busy} error={error} onConfirm={() => removeConnection(key)} onCancel={() => { setConfirm(null); setError(""); }} />}
        <div hidden={!open}>
          <ConnectionEditor
            key={key}
            previousKey={key}
            initialBuffer={connectionBuffer(key, savedDraft)}
            savedDraft={savedDraft}
            catalogs={catalogs}
            usage={usage}
            api={api}
            persist={persist}
            reportDirty={reportEditorDirty}
            autoPick={autoPickRef.current === key ? () => { autoPickRef.current = null; } : null}
          />
        </div>
      </article>;
    })}
  </section>;
}
