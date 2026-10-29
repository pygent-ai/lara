import React, { useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, RefreshCw, Save, Trash2 } from "lucide-react";
import { ModelCapabilitiesEditor } from "./ModelCapabilitiesEditor.jsx";
import { emptyNativeModel, modelDiscoveryRequest, protocolLabel } from "./settingsModel.js";

// Substring filter over the provider's discovered models; keeps the provider's
// own order so the list never jumps around while typing.
export function filterDiscoveredModels(discovered, query) {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return discovered;
  return discovered.filter((item) => String(item.id || "").toLocaleLowerCase().includes(needle));
}

// Add-model row for one connection. It looks exactly like a saved model row:
// one collapsed head line with 模型选择 / 本地名称 / API 协议 plus the icon
// actions (测试 / 保存 / 删除), while the capability configuration stays
// folded below until the chevron opens it. 保存 commits the model into the
// connection buffer and the row turns into a normal model row without the
// save action. The caller owns the connection buffer.
export function AddModelForm({ connection, credentialValue, catalogs, api, defaultModelKey, onAdd, onCancel }) {
  const protocolChoices = Object.keys(connection?.protocols || {});
  const provider = connection?.provider || "";
  const [modelId, setModelId] = useState("");
  const [localName, setLocalName] = useState(defaultModelKey || "");
  const [protocol, setProtocol] = useState(protocolChoices[0] || "");
  const [capabilities, setCapabilities] = useState(() => structuredClone(emptyNativeModel("", protocolChoices[0] || "").capabilities));
  const [discovered, setDiscovered] = useState([]);
  const [discovery, setDiscovery] = useState({ status: "idle" });
  const [refresh, setRefresh] = useState(0);
  // The native datalist popup cannot scroll and gets clipped by the window
  // once a provider returns hundreds of models, so the suggestions render in
  // this capped, scrollable listbox instead.
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [highlight, setHighlight] = useState(-1);
  // The add form opens expanded: picking the 能力模板 is part of adding a
  // model, not a follow-up edit on a saved row.
  const [expanded, setExpanded] = useState(true);
  const controlRef = useRef(null);
  const optionsRef = useRef(null);
  const request = useMemo(() => (
    connection && protocolChoices[0]
      ? modelDiscoveryRequest({ connections: { c: connection }, credentialValues: connection.credential?.env ? { [connection.credential.env]: credentialValue || "" } : {} }, "c")
      : null
  ), [connection, credentialValue]);
  const requestSignature = JSON.stringify(request);

  useEffect(() => {
    const controller = new AbortController();
    setDiscovered([]);
    if (!api?.discoverModels || !request) {
      setDiscovery((current) => ({ ...current, status: "unavailable" }));
      return () => controller.abort();
    }
    setDiscovery((current) => ({ ...current, status: "loading" }));
    api.discoverModels(request, { signal: controller.signal }).then((response) => {
      if (controller.signal.aborted) return;
      const models = Array.isArray(response?.models) ? response.models : [];
      setDiscovered(models);
      setDiscovery((current) => ({ ...current, status: models.length ? "ready" : "empty" }));
    }).catch((error) => {
      if (controller.signal.aborted || error?.name === "AbortError") return;
      setDiscovery((current) => ({ ...current, status: "error" }));
    });
    return () => controller.abort();
  }, [api, requestSignature, refresh]);

  const filtered = useMemo(
    () => filterDiscoveredModels(discovered, modelId),
    [discovered, modelId],
  );

  useEffect(() => { setHighlight(-1); }, [modelId, optionsOpen]);

  // Close the listbox on any interaction outside the model ID input.
  useEffect(() => {
    if (!optionsOpen) return;
    function onPointerDown(event) {
      if (!controlRef.current?.contains(event.target)) setOptionsOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [optionsOpen]);

  useEffect(() => { if (optionsOpen && highlight >= 0) optionsRef.current?.children[highlight]?.scrollIntoView({ block: "nearest" }); }, [highlight, optionsOpen]);

  function pick(id) {
    updateModelId(id);
    setOptionsOpen(false);
  }

  // A known catalog entry pre-fills capabilities, mirroring how a saved model
  // row adopts them when its 服务商模型 ID changes.
  function knownCapabilities(nextId, nextProtocol) {
    const known = catalogs?.model_capabilities?.find(item => item.provider === provider && item.protocol === nextProtocol && item.model_id === nextId);
    return known?.capabilities ? structuredClone(known.capabilities) : null;
  }

  function updateModelId(nextId) {
    setModelId(nextId);
    setOptionsOpen(discovered.length > 0);
    const known = knownCapabilities(nextId, protocol);
    if (known) setCapabilities(known);
  }

  function updateProtocol(nextProtocol) {
    setProtocol(nextProtocol);
    const known = knownCapabilities(modelId, nextProtocol);
    if (known) setCapabilities(known);
  }

  function onInputKeyDown(event) {
    if (event.key === "Tab") { setOptionsOpen(false); return; }
    if (event.key === "Escape") { setOptionsOpen(false); return; }
    if (!optionsOpen || filtered.length === 0) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setHighlight((current) => {
        const step = event.key === "ArrowDown" ? 1 : -1;
        return Math.max(0, Math.min(filtered.length - 1, (event.key === "ArrowDown" ? current + 1 : current < 0 ? 0 : current - 1)));
      });
    } else if (event.key === "Enter" && highlight >= 0) {
      event.preventDefault();
      pick(filtered[highlight].id);
    }
  }

  const discoveryText = {
    loading: "正在从连接获取模型…",
    ready: `已获取 ${discovered.length} 个模型，也可以手动输入`,
    empty: "服务商没有返回模型，可手动输入模型 ID",
    error: "自动获取失败，可手动输入或重试",
    unavailable: "当前环境不支持自动获取，请手动输入模型 ID",
    idle: "",
  }[discovery.status];

  const suggestionsOpen = optionsOpen && filtered.length > 0;

  function submit() {
    if (!modelId.trim()) return;
    onAdd({ key: localName.trim(), modelId: modelId.trim(), protocol, capabilities });
  }

  return <article className={`model-route-card pool-row${expanded ? " pool-row-open" : ""} connection-model-new`} aria-label="添加模型表单">
    <div className="pool-row-head model-row-head">
      <button type="button" className="pool-row-main" aria-expanded={expanded} title={expanded ? "收起能力配置" : "展开能力配置"} onClick={() => setExpanded((current) => !current)}><ChevronRight size={16} aria-hidden="true" /></button>
      <div className="head-field field-select">
        <div className="add-model-id-control" ref={controlRef}>
          <input
            value={modelId}
            onChange={event => updateModelId(event.target.value)}
            onFocus={() => setOptionsOpen(discovered.length > 0)}
            onKeyDown={onInputKeyDown}
            role="combobox"
            aria-expanded={suggestionsOpen}
            aria-controls="add-model-options"
            aria-autocomplete="list"
            aria-label="模型选择"
            autoComplete="off"
            spellCheck={false}
            placeholder={discovery.status === "loading" ? "正在获取模型…" : "选择或输入模型 ID"}
          />
          {suggestionsOpen && (
            <div className="add-model-options" id="add-model-options" role="listbox" ref={optionsRef}>
              {filtered.map((item, index) => (
                <button
                  type="button"
                  role="option"
                  aria-selected={index === highlight}
                  key={`${item.id}::${index}`}
                  className={index === highlight ? "add-model-option active" : "add-model-option"}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => pick(item.id)}
                >
                  <span className="add-model-option-id">{item.id}</span>
                  <span className="add-model-option-owner">{item.owned_by || ""}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
      <div className="head-field field-name">
        <span className="head-tag">名称</span>
        <input value={localName} onChange={event => setLocalName(event.target.value)} aria-label="本地名称" title="本地名称（仅本地引用，不会发送给服务商）" />
      </div>
      <div className="head-field field-protocol">
        <span className="head-tag">协议</span>
        <select value={protocol} onChange={event => updateProtocol(event.target.value)} aria-label="API 协议" title="API 协议（当前连接提供的协议）">{protocolChoices.map(key => <option key={key} value={key}>{protocolLabel(key)}</option>)}</select>
      </div>
      <div className="model-head-actions">
        <button type="button" className="icon-action" title="测试连接并获取模型" aria-label="测试连接并获取模型" disabled={!api?.discoverModels || !request || discovery.status === "loading"} onClick={() => setRefresh((current) => current + 1)}><RefreshCw size={14} aria-hidden="true" /></button>
        <button type="button" className="icon-action" title="保存模型" aria-label="保存模型" disabled={!modelId.trim()} onClick={submit}><Save size={14} aria-hidden="true" /></button>
        <button type="button" className="icon-action" title="删除并关闭表单" aria-label="删除并关闭表单" onClick={onCancel}><Trash2 size={14} aria-hidden="true" /></button>
      </div>
    </div>
    <div className="pool-editor-shell">
      <div className="pool-editor-inner">
        <div className="add-model-editor">
          <small className={`add-model-discovery ${discovery.status}`} role="status">{discoveryText}</small>
          <ModelCapabilitiesEditor value={capabilities} onChange={setCapabilities} catalogs={catalogs} provider={provider} protocol={protocol} />
          <p className="add-model-hint">使用 {protocolLabel(protocol)}。</p>
        </div>
      </div>
    </div>
  </article>;
}
