import React, { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Plus, Star, Trash2, X } from "lucide-react";
import { SectionHead } from "./SectionHead.jsx";
import { ConfirmDelete, errorText } from "./ConfirmDelete.jsx";
import { capabilitySummary, commitGroup, commitGroupDelete, deletionImpact, groupDirty, groupError, uniqueKey } from "./settingsModel.js";

const NEW = "__new__";

function modelLine(savedDraft, key) {
  const model = savedDraft.models[key];
  if (!model) return { title: key, meta: "模型已删除" };
  const provider = savedDraft.connections[model.connection]?.provider || model.connection;
  return { title: model.model_id || key, meta: `${provider} · ${capabilitySummary(model.capabilities)}${key !== model.model_id ? ` · ${key}` : ""}` };
}

// Model groups: a list on the left, one editor on the right. A group is a
// primary model plus the fallbacks tried in order; the editor saves one
// group at a time, deletion and "set default" commit immediately.
export function GroupSettings({ savedDraft, usage, persist, reportDirty }) {
  const groupNames = Object.keys(savedDraft.modelGroups);
  const [selected, setSelected] = useState(() => groupNames[0] || null);
  const [buffer, setBuffer] = useState(() => (groupNames[0] ? { name: groupNames[0], models: [...savedDraft.modelGroups[groupNames[0]].models] } : null));
  const [nameTouched, setNameTouched] = useState(true);
  const [confirm, setConfirm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const previousName = selected === NEW ? null : selected;

  // (Re)load the buffer whenever the selection or the saved group changes.
  useEffect(() => {
    if (selected === NEW) { setBuffer({ name: "", models: [] }); setNameTouched(false); return; }
    if (selected && savedDraft.modelGroups[selected]) { setBuffer({ name: selected, models: [...savedDraft.modelGroups[selected].models] }); setNameTouched(true); return; }
    const first = Object.keys(savedDraft.modelGroups)[0] || null;
    setSelected(first);
    setBuffer(first ? { name: first, models: [...savedDraft.modelGroups[first].models] } : null);
  }, [selected, savedDraft]);

  const dirty = Boolean(buffer) && groupDirty(buffer, savedDraft, previousName);
  useEffect(() => { reportDirty("groups", dirty); return () => reportDirty("groups", false); }, [dirty, reportDirty]);

  const validation = buffer ? groupError(buffer, savedDraft, previousName) : "";
  const available = Object.keys(savedDraft.models).filter((key) => !buffer?.models.includes(key));

  function update(patch) {
    setError("");
    setBuffer((current) => ({ ...current, ...patch }));
  }

  // Picking a primary that already sits among the fallbacks promotes it; an
  // untouched name follows the primary so most groups never need naming.
  function setPrimary(key) {
    const rest = buffer.models.slice(1).filter((item) => item !== key);
    update({ models: key ? [key, ...rest] : rest, ...(nameTouched || !key ? {} : { name: previousName === key ? key : uniqueKey(key, savedDraft.modelGroups) }) });
  }

  function move(index, offset) {
    const models = [...buffer.models];
    const target = index + offset;
    if (target < 0 || target >= models.length) return;
    [models[index], models[target]] = [models[target], models[index]];
    update({ models });
  }

  async function save() {
    setError("");
    setBusy(true);
    try {
      await persist(commitGroup(savedDraft, previousName, buffer));
      setSelected(buffer.name.trim());
    } catch (saveError) {
      setError(errorText(saveError));
    } finally {
      setBusy(false);
    }
  }

  async function remove(name) {
    setBusy(true);
    try {
      await persist(commitGroupDelete(savedDraft, name));
      setConfirm(null);
      if (selected === name) setSelected(null);
    } catch (deleteError) {
      setError(errorText(deleteError));
    } finally {
      setBusy(false);
    }
  }

  async function setDefault(name) {
    setError("");
    try {
      await persist({ ...savedDraft, defaultModelGroup: name });
    } catch (saveError) {
      setError(errorText(saveError));
    }
  }

  if (!Object.keys(savedDraft.models).length) {
    return <section className="settings-card" aria-label="模型组">
      <SectionHead title="模型组" description="一个模型组由一个主模型和按顺序回退的备用模型组成，对话创建时选定模型组。" />
      <p className="settings-empty">还没有可用的模型。先在「连接与模型」中添加并保存模型，再回到这里组织模型组。</p>
    </section>;
  }

  return <section className="settings-card group-settings" aria-label="模型组">
    <SectionHead title="模型组" description="一个模型组由一个主模型和按顺序回退的备用模型组成；对话创建时选定模型组，之后可在组内切换首选模型。" />
    <div className="group-layout">
      <aside className="group-list" aria-label="模型组列表">
        {groupNames.map((name) => {
          const group = savedDraft.modelGroups[name];
          const isDefault = savedDraft.defaultModelGroup === name;
          return <div key={name} className={`group-list-item${selected === name ? " selected" : ""}`}>
            <button type="button" className="group-list-main" aria-current={selected === name ? "true" : undefined} onClick={() => { setSelected(name); setConfirm(null); }}>
              <strong>{name}{isDefault && <span className="default-chip">默认</span>}</strong>
              <small>{group.models.map((key) => modelLine(savedDraft, key).title).join(" → ")}</small>
            </button>
            <div className="group-list-actions">
              {!isDefault && <button type="button" className="icon-action" title="设为默认模型组" aria-label={`设为默认模型组 ${name}`} onClick={() => setDefault(name)}><Star size={14} aria-hidden="true" /></button>}
              <button type="button" className="icon-action" title="删除模型组" aria-label={`删除模型组 ${name}`} onClick={() => setConfirm(name)}><Trash2 size={14} aria-hidden="true" /></button>
            </div>
            {confirm === name && <ConfirmDelete title={`删除模型组 ${name}？`} impact={deletionImpact("group", name, savedDraft, usage)} busy={busy} error={error} onConfirm={() => remove(name)} onCancel={() => { setConfirm(null); setError(""); }} />}
          </div>;
        })}
        <button type="button" className={`group-list-new${selected === NEW ? " selected" : ""}`} onClick={() => { setSelected(NEW); setConfirm(null); }}><Plus size={14} aria-hidden="true" /> 新建模型组</button>
      </aside>

      {buffer && <div className="group-editor" aria-label={previousName ? `编辑模型组 ${previousName}` : "新建模型组"}>
        <SectionHead title={previousName ? previousName : "新建模型组"} dirty={dirty} onSave={save} saveLabel={previousName ? `保存模型组 ${previousName}` : "保存新模型组"} saveDisabled={Boolean(validation) || busy} saveTitle={validation} />
        {error && !confirm && <p className="editor-error" role="alert">{error}</p>}
        <label>
          <span>名称</span>
          <input value={buffer.name} onChange={(event) => { setNameTouched(true); update({ name: event.target.value }); }} placeholder="例如 coding" aria-label="模型组名称" />
        </label>
        <label>
          <span>主模型</span>
          <select value={buffer.models[0] || ""} onChange={(event) => setPrimary(event.target.value)} aria-label="主模型">
            <option value="">请选择主模型</option>
            {Object.keys(savedDraft.models).map((key) => <option key={key} value={key}>{modelLine(savedDraft, key).title}{key !== savedDraft.models[key].model_id ? ` (${key})` : ""}</option>)}
          </select>
          {buffer.models[0] && <small>{modelLine(savedDraft, buffer.models[0]).meta}</small>}
        </label>
        <div className="fallback-list" aria-label="备用模型">
          <span className="field-label">备用模型 <small>主模型失败后按顺序尝试</small></span>
          {buffer.models.slice(1).map((key, index) => {
            const line = modelLine(savedDraft, key);
            const position = index + 1;
            return <div className="fallback-item" key={key}>
              <span className="fallback-rank">{position}</span>
              <span className="fallback-copy"><strong>{line.title}</strong><small>{line.meta}</small></span>
              <span className="fallback-actions">
                <button type="button" className="icon-action" disabled={position === 1} title="提前" aria-label={`提前 ${line.title}`} onClick={() => move(position, -1)}><ArrowUp size={14} aria-hidden="true" /></button>
                <button type="button" className="icon-action" disabled={position === buffer.models.length - 1} title="推后" aria-label={`推后 ${line.title}`} onClick={() => move(position, 1)}><ArrowDown size={14} aria-hidden="true" /></button>
                <button type="button" className="icon-action" title="移出模型组" aria-label={`移出 ${line.title}`} onClick={() => update({ models: buffer.models.filter((item) => item !== key) })}><X size={14} aria-hidden="true" /></button>
              </span>
            </div>;
          })}
          {buffer.models.length <= 1 && <p className="field-note">没有备用模型；主模型失败时直接报错。</p>}
          {available.length > 0 && <select className="fallback-add" value="" aria-label="添加备用模型" onChange={(event) => { if (event.target.value) update({ models: [...buffer.models, event.target.value] }); }}>
            <option value="">+ 添加备用模型…</option>
            {available.map((key) => <option key={key} value={key}>{modelLine(savedDraft, key).title}{key !== savedDraft.models[key].model_id ? ` (${key})` : ""}</option>)}
          </select>}
        </div>
      </div>}
    </div>
  </section>;
}
