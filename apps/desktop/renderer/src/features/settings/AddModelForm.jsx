import React, { useEffect, useMemo, useRef, useState } from "react";
import { Plus, RefreshCw } from "lucide-react";
import { modelDiscoveryRequest, protocolLabel } from "./settingsModel.js";

// Substring filter over the provider's discovered models; keeps the provider's
// own order so the list never jumps around while typing.
export function filterDiscoveredModels(discovered, query) {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return discovered;
  return discovered.filter((item) => String(item.id || "").toLocaleLowerCase().includes(needle));
}

// Add-model form for one connection; the caller owns the connection buffer and
// turns the picked model ID into a draft model entry.
export function AddModelForm({ connection, credentialValue, api, onAdd, onCancel }) {
  const [modelId, setModelId] = useState("");
  const [discovered, setDiscovered] = useState([]);
  const [discovery, setDiscovery] = useState({ status: "idle" });
  const [refresh, setRefresh] = useState(0);
  // The native datalist popup cannot scroll and gets clipped by the window
  // once a provider returns hundreds of models, so the suggestions render in
  // this capped, scrollable listbox instead.
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [highlight, setHighlight] = useState(-1);
  const controlRef = useRef(null);
  const optionsRef = useRef(null);
  const protocol = Object.keys(connection?.protocols || {})[0];
  const request = useMemo(() => (
    connection && protocol
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

  // Close the listbox on any interaction outside the input + refresh button.
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
    setModelId(id);
    setOptionsOpen(false);
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

  return <section className="add-model-form" aria-label="添加模型表单">
    <div className="add-model-fields">
      <label className="add-model-id-field">
        <span>模型 ID</span>
        <div className="add-model-id-control" ref={controlRef}>
          <input
            value={modelId}
            onChange={event => { setModelId(event.target.value); setOptionsOpen(discovered.length > 0); }}
            onFocus={() => setOptionsOpen(discovered.length > 0)}
            onKeyDown={onInputKeyDown}
            role="combobox"
            aria-expanded={suggestionsOpen}
            aria-controls="add-model-options"
            aria-autocomplete="list"
            autoComplete="off"
            spellCheck={false}
            placeholder={discovery.status === "loading" ? "正在获取模型…" : "选择或输入模型 ID"}
          />
          <button type="button" disabled={!api?.discoverModels || discovery.status === "loading"} onClick={() => setRefresh((current) => current + 1)} aria-label="重新获取模型" title="重新获取模型"><RefreshCw size={14} aria-hidden="true" /></button>
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
        <small className={`add-model-discovery ${discovery.status}`} role="status">{discoveryText}</small>
      </label>
      <button type="button" className="route-add" disabled={!modelId.trim()} onClick={() => onAdd(modelId.trim())}><Plus size={16} /> 确认添加</button>
      <button type="button" className="plain-action" onClick={onCancel}>取消添加</button>
    </div>
    <p className="add-model-hint">使用 {protocolLabel(protocol)}。</p>
  </section>;
}
