import React, { useState } from "react";
import { PURPOSES, MODALITIES, purposeCapabilities, capabilitySignature, updateCapability, capabilityTags, templateOptions, capabilitiesEqual } from "./modelCapabilities.js";

export function ModelCapabilitiesEditor({ value, onChange, catalogs, provider = "", protocol = "", modelKey = "", renameModel }) {
  const [chosen, setChosen] = useState(() => PURPOSES.find(item => capabilitySignature(purposeCapabilities(item.id, value)) === capabilitySignature(value))?.id || null);
  const [baseline, setBaseline] = useState(() => capabilitySignature(value));
  const [notice, setNotice] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [picked, setPicked] = useState(null);
  const currentSignature = capabilitySignature(value);
  const exact = PURPOSES.find(item => capabilitySignature(purposeCapabilities(item.id, value)) === currentSignature);
  const selected = chosen || exact?.id || "custom";
  const adjusted = chosen && chosen !== "custom" && currentSignature !== baseline;
  const templates = templateOptions(catalogs, { provider, protocol });
  const pickedEntry = picked ? templates.find(item => item.key === picked.key) : null;
  const matched = (pickedEntry && capabilitiesEqual(value, picked.snapshot) ? pickedEntry : null)
    || templates.find(item => capabilitiesEqual(value, item.capabilities))
    || null;
  function apply(id, next) {
    const before = capabilityTags(value), after = capabilityTags(next);
    const added = after.filter(tag => !before.includes(tag));
    const removed = before.filter(tag => !after.includes(tag));
    setNotice([added.length ? `开启：${added.join("、")}` : "", removed.length ? `关闭：${removed.join("、")}` : "", "长度限制已保留"].filter(Boolean).join("；"));
    setChosen(id); setBaseline(capabilitySignature(next)); setPicked(null); onChange(next);
  }
  function applyTemplate(entry) {
    const next = structuredClone(entry.capabilities);
    const before = capabilityTags(value), after = capabilityTags(next);
    const added = after.filter(tag => !before.includes(tag));
    const removed = before.filter(tag => !after.includes(tag));
    setNotice([`已应用 Pygent 模板：${entry.model_id}`, added.length ? `开启：${added.join("、")}` : "", removed.length ? `关闭：${removed.join("、")}` : ""].filter(Boolean).join("；") + "。修改任意值即成为自定义配置。");
    setChosen(null); setBaseline(capabilitySignature(next)); setPicked({ key: entry.key, snapshot: next }); setExpanded(true); onChange(next);
  }
  function edit(section, field, next) { onChange(updateCapability(value, section, field, next)); }
  function choices(title, section, field, options) {
    const selectedValues = value?.[section]?.[field] || [];
    return <fieldset className="capability-options"><legend>{title}</legend>{options.map(([id, label]) => <label key={id}><input type="checkbox" checked={selectedValues.includes(id)} onChange={event => edit(section, field, event.target.checked ? [...selectedValues, id] : selectedValues.filter(item => item !== id))} /><span>{label}</span></label>)}</fieldset>;
  }
  function toggle(label, section, field, disabled = false) {
    return <label className="capability-toggle"><input type="checkbox" checked={Boolean(value?.[section]?.[field])} disabled={disabled} onChange={event => edit(section, field, event.target.checked)} /><span>{label}</span></label>;
  }
  return <section className="capability-editor" aria-label="模型能力">
    <div className="capability-purpose">
      <label><span>Pygent 模型模板 · 实际模型{matched ? "" : " · 自定义"}</span><select value={matched?.key ?? ""} onChange={event => { const entry = templates.find(item => item.key === event.target.value); if (entry) applyTemplate(entry); }}>
        <option value="">自定义配置 — 手动组合能力</option>
        {templates.map(item => <option key={item.key} value={item.key}>{item.provider} · {item.model_id} · {item.protocol}</option>)}
      </select></label>
      <label><span>模型用途{adjusted ? " · 已调整" : ""}</span><select value={selected} onChange={event => { const id = event.target.value; if (id === "custom") { setChosen(id); setExpanded(true); } else apply(id, purposeCapabilities(id, value)); }}>
        {PURPOSES.map(item => <option key={item.id} value={item.id}>{item.name} — {item.description}</option>)}
        <option value="custom">自定义 — 自行组合模型能力</option>
      </select></label>
      <p className="capability-hint">模板是 Pygent 目录中的实际模型，展开后是它的具体能力配置（含上下文与输出限制）；修改任意值即成为可重命名的自定义配置。用途是手动配置模板，不代表服务商已支持这些能力。</p>
      {!matched && renameModel && <label className="capability-rename"><span>自定义配置名称</span><input key={modelKey} defaultValue={modelKey} onBlur={event => renameModel(modelKey, event.target.value)} /></label>}
    </div>
    <div className="capability-tags" aria-label="当前能力">{capabilityTags(value).map(tag => <span key={tag}>{tag}</span>)}{!capabilityTags(value).length && <span>尚未声明能力</span>}</div>
    {notice && <p className="capability-notice" role="status">{notice}</p>}
    <details open={expanded} onToggle={event => setExpanded(event.currentTarget.open)} className="capability-details">
      <summary>详细调整 <span>输入输出、工具、推理与长度限制</span></summary>
      <div className="capability-detail-body">
        {choices("输入类型", "modalities", "input", Object.entries(MODALITIES).filter(([key]) => key !== "embedding"))}
        {choices("输出类型", "modalities", "output", Object.entries(MODALITIES))}
        {choices("流式输出（仅可选择已启用的输出类型）", "streaming", "output", (value?.modalities?.output || []).map(key => [key, MODALITIES[key]]))}
        <fieldset className="capability-options"><legend>工具调用</legend>{toggle("允许调用工具", "tools", "call")}{toggle("并行调用", "tools", "parallel")}</fieldset>
        {choices("工具选择模式", "tools", "choice", [["none", "不调用"], ["auto", "自动"], ["required", "必须调用"], ["named", "指定工具"]])}
        <fieldset className="capability-options"><legend>结构化输出</legend>{toggle("JSON Object", "structured_output", "json_object")}{toggle("JSON Schema", "structured_output", "json_schema")}</fieldset>
        <fieldset className="capability-options"><legend>推理能力</legend>{toggle("支持推理", "reasoning", "supported")}{toggle("可控制推理", "reasoning", "controllable", !value?.reasoning?.supported)}</fieldset>
        <div className="model-route-grid">{[["context_tokens", "上下文 tokens"], ["max_output_tokens", "最大输出 tokens"]].map(([key, label]) => <label key={key}><span>{label}</span><input type="number" min="1" step="1" placeholder="未指定" value={value?.limits?.[key] ?? ""} onChange={event => edit("limits", key, event.target.value === "" ? null : Number(event.target.value))} /></label>)}</div>
      </div>
    </details>
  </section>;
}
