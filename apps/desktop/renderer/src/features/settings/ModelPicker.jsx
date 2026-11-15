import React, { useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw, Search } from "lucide-react";
import { modelDiscoveryRequest, protocolLabel } from "./settingsModel.js";

export function filterDiscoveredModels(discovered, query) {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return discovered;
  return discovered.filter((item) => String(item.id || "").toLocaleLowerCase().includes(needle));
}

// Multi-select picker for adding models to one connection. It asks the
// provider for its model list, lets the user tick several at once (or type
// an ID the provider does not list) and hands the chosen IDs back; the
// caller turns them into model records.
export function ModelPicker({ connection, credentialValue, existingIds, api, onAdd, onCancel }) {
  const protocols = Object.keys(connection.protocols || {});
  const [protocol, setProtocol] = useState(protocols[0] || "");
  const [query, setQuery] = useState("");
  const [discovered, setDiscovered] = useState([]);
  const [status, setStatus] = useState("idle");
  const [attempt, setAttempt] = useState(0);
  const [picked, setPicked] = useState([]);
  const inputRef = useRef(null);
  const request = useMemo(() => modelDiscoveryRequest(connection, protocol, credentialValue), [connection, protocol, credentialValue]);
  const needsKey = !connection.credential?.none && connection.credential_source === "missing" && !credentialValue?.trim();

  useEffect(() => {
    const controller = new AbortController();
    setDiscovered([]);
    if (!api?.discoverModels || !request || needsKey) { setStatus(needsKey ? "needs-key" : "unavailable"); return () => controller.abort(); }
    setStatus("loading");
    api.discoverModels(request, { signal: controller.signal }).then((response) => {
      if (controller.signal.aborted) return;
      const models = Array.isArray(response?.models) ? response.models : [];
      setDiscovered(models);
      setStatus(models.length ? "ready" : "empty");
    }).catch((error) => {
      if (controller.signal.aborted || error?.name === "AbortError") return;
      setStatus("error");
    });
    return () => controller.abort();
  }, [api, JSON.stringify(request), needsKey, attempt]);

  useEffect(() => { inputRef.current?.focus(); }, []);

  const filtered = useMemo(() => filterDiscoveredModels(discovered, query), [discovered, query]);
  const typed = query.trim();
  const typedIsNew = typed && !discovered.some((item) => item.id === typed) && !existingIds.includes(typed);
  const statusText = {
    loading: "正在从服务商获取模型列表…",
    ready: `服务商返回 ${discovered.length} 个模型，勾选后添加；列表里没有的可直接输入 ID。`,
    empty: "服务商没有返回模型列表，请输入模型 ID。",
    error: "获取模型列表失败，可重试或直接输入模型 ID。",
    unavailable: "当前环境无法自动获取，请输入模型 ID。",
    "needs-key": "填写 API Key 后可自动获取模型列表；也可以直接输入模型 ID。",
    idle: "",
  }[status];

  function toggle(id) {
    setPicked((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  function submit() {
    const ids = [...picked];
    if (typedIsNew && !ids.includes(typed)) ids.push(typed);
    if (!ids.length) return;
    onAdd(ids.map((id) => ({ modelId: id, protocol })));
  }

  const count = picked.length + (typedIsNew && !picked.includes(typed) ? 1 : 0);

  return <div className="model-picker" role="dialog" aria-label="添加模型">
    <div className="model-picker-search">
      <Search size={14} aria-hidden="true" />
      <input ref={inputRef} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索模型，或输入服务商未列出的模型 ID" aria-label="模型 ID" spellCheck={false} autoComplete="off"
        onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); submit(); } if (event.key === "Escape") onCancel(); }} />
      {protocols.length > 1 && <select value={protocol} onChange={(event) => setProtocol(event.target.value)} aria-label="API 协议">{protocols.map((key) => <option key={key} value={key}>{protocolLabel(key)}</option>)}</select>}
      <button type="button" className="icon-action" title="重新获取模型列表" aria-label="重新获取模型列表" disabled={!api?.discoverModels || !request || needsKey || status === "loading"} onClick={() => setAttempt((current) => current + 1)}><RefreshCw size={14} aria-hidden="true" /></button>
    </div>
    <p className={`model-picker-status ${status}`} role="status">{statusText}</p>
    <div className="model-picker-list" role="listbox" aria-multiselectable="true" aria-label="可添加的模型">
      {typedIsNew && <label className="model-picker-item manual">
        <input type="checkbox" checked readOnly aria-label={`添加 ${typed}`} />
        <span><strong>{typed}</strong><small>手动输入</small></span>
      </label>}
      {filtered.map((item) => {
        const added = existingIds.includes(item.id);
        return <label key={item.id} className={`model-picker-item${added ? " added" : ""}`}>
          <input type="checkbox" disabled={added} checked={added || picked.includes(item.id)} onChange={() => toggle(item.id)} aria-label={item.id} />
          <span><strong>{item.id}</strong><small>{added ? "已添加" : item.owned_by || ""}</small></span>
        </label>;
      })}
      {!filtered.length && !typedIsNew && status === "ready" && <p className="field-note">没有匹配的模型。</p>}
    </div>
    <div className="model-picker-actions">
      <button type="button" className="plain-action" onClick={onCancel}>取消</button>
      <button type="button" className="primary-action" disabled={!count} onClick={submit}>{count ? `添加 ${count} 个模型` : "添加模型"}</button>
    </div>
  </div>;
}
