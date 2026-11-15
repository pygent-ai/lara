import React, { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { useSettingsData } from "./useSettingsData.js";
import { settingsForSave, settingsToDraft } from "./settingsModel.js";
import { ConnectionSettings } from "./ConnectionSettings.jsx";
import { GroupSettings } from "./GroupSettings.jsx";
import { WorkspaceSettings } from "./WorkspaceSettings.jsx";
import { RuntimeSettings } from "./RuntimeSettings.jsx";
import "./settings.css";

const SECTIONS = [
  ["models", "模型", "连接、模型与模型组"],
  ["workspace", "项目与 Agent", "工作目录与执行角色"],
  ["runtime", "运行与权限", "执行步数、上下文、重试与审批"],
];

const MODEL_TABS = [
  ["connections", "连接与模型"],
  ["groups", "模型组"],
];

const FIELD_SECTIONS = {
  workspace: ["workspaceRoot", "agent"],
  runtime: ["maxSteps", "contextWindow", "approvalsEnabled", "retry"],
};

// Full-page settings. `savedDraft` is the last persisted state; the
// connection and group editors keep their own buffers and commit slices over
// it through `persist`, while the workspace/runtime tabs edit `fields` and
// save their own field list. Nothing is saved globally.
export function SettingsPage({ settings, disabled, onClose, onPersist, api }) {
  const [section, setSection] = useState("models");
  const [modelTab, setModelTab] = useState("connections");
  const [confirmClose, setConfirmClose] = useState(false);
  const { snapshot, catalogs, usage, refreshUsage } = useSettingsData(settings, api);
  const [savedDraft, setSavedDraft] = useState(() => settingsToDraft(snapshot));
  const [fields, setFields] = useState(() => settingsToDraft(snapshot));
  // The fetched snapshot replaces the app-level cache the page opened with;
  // editors remount on it so no buffer keeps the pre-fetch state.
  const [snapshotVersion, setSnapshotVersion] = useState(0);
  useEffect(() => {
    const draft = settingsToDraft(snapshot);
    setSavedDraft(draft);
    setFields(draft);
    setSnapshotVersion((current) => current + 1);
  }, [snapshot]);
  // Editors report their unsaved state so the close guard can warn about it.
  const dirtyEditorsRef = useRef({});
  const [editorsDirty, setEditorsDirty] = useState(false);
  const reportDirty = useCallback((id, value) => {
    dirtyEditorsRef.current[id] = value;
    setEditorsDirty(Object.values(dirtyEditorsRef.current).some(Boolean));
  }, []);
  const [sectionError, setSectionError] = useState("");
  const panelRef = useRef(null);

  const fieldsDirty = (names) => names.some((name) => JSON.stringify(fields[name]) !== JSON.stringify(savedDraft[name]));
  const workspaceDirty = fieldsDirty(FIELD_SECTIONS.workspace);
  const runtimeDirty = fieldsDirty(FIELD_SECTIONS.runtime);
  const dirty = editorsDirty || workspaceDirty || runtimeDirty;

  // Persist a full saved draft and rebase the baseline on the server's
  // response, so credential sources and server-side normalisation show up
  // without a reload and typed secrets are never sent twice.
  const persist = useCallback(async (nextSaved, options = {}) => {
    const saved = await onPersist(nextSaved, options);
    const rebased = saved ? settingsToDraft(saved) : { ...nextSaved, credentialValues: {} };
    setSavedDraft(rebased);
    setFields((current) => ({ ...rebased, ...Object.fromEntries([...FIELD_SECTIONS.workspace, ...FIELD_SECTIONS.runtime].map((name) => [name, current[name]])) }));
    refreshUsage();
    return rebased;
  }, [onPersist, refreshUsage]);

  function setField(name, value) {
    setFields((current) => ({ ...current, [name]: value }));
  }

  async function saveFields(names, options = {}) {
    setSectionError("");
    try {
      const merged = { ...savedDraft, ...Object.fromEntries(names.map((name) => [name, fields[name]])) };
      const nextSaved = options.workspace ? settingsForSave(merged, { workspace_root: savedDraft.workspaceRoot, agent: savedDraft.agent }) : merged;
      const workspaceChanged = options.workspace && nextSaved.workspaceRoot.trim() !== savedDraft.workspaceRoot.trim();
      const rebased = await persist(nextSaved, { workspaceChanged });
      if (options.workspace) setFields((current) => ({ ...current, agent: rebased.agent }));
    } catch (error) {
      setSectionError(typeof error?.message === "string" && error.message ? error.message : "保存失败，请稍后再试。");
    }
  }

  function requestClose() {
    if (disabled) return;
    if (dirty) setConfirmClose(true);
    else onClose();
  }

  function navigate(id) {
    setSection(id);
    setConfirmClose(false);
    panelRef.current?.querySelector(".settings-form")?.scrollTo(0, 0);
  }

  useEffect(() => {
    const previous = document.activeElement;
    panelRef.current?.querySelector("button")?.focus();
    return () => previous?.focus?.();
  }, []);

  const current = SECTIONS.find(([id]) => id === section);
  const pending = [editorsDirty && "模型", workspaceDirty && "项目与 Agent", runtimeDirty && "运行与权限"].filter(Boolean);

  return <div className="settings-page">
    <section className="settings-panel" aria-label="Settings" role="region" ref={panelRef} onKeyDown={(event) => {
      if (event.key === "Escape") { event.stopPropagation(); confirmClose ? setConfirmClose(false) : requestClose(); }
    }}>
      <header className="settings-header">
        <h2>设置</h2>
        <button className="plain-action settings-back" type="button" disabled={disabled} onClick={requestClose} aria-label="Close settings" title="Close settings">
          <ArrowLeft size={18} aria-hidden="true" /> 返回工作区
        </button>
      </header>

      <div className="settings-layout">
        <nav className="settings-nav" aria-label="配置分类">
          {SECTIONS.map(([id, title, description]) => <button type="button" key={id} aria-current={section === id ? "page" : undefined} onClick={() => navigate(id)}>
            <strong>{title}</strong><small>{description}</small>
          </button>)}
        </nav>
        <div className="settings-form">
          <div className="settings-page-heading">
            <h3>{current[1]}</h3>
            {section === "models" && <nav className="settings-subtabs" aria-label="模型配置分层">
              {MODEL_TABS.map(([id, title]) => <button type="button" key={id} aria-current={modelTab === id ? "true" : undefined} onClick={() => setModelTab(id)}>{title}</button>)}
            </nav>}
          </div>
          <fieldset className="settings-fields" disabled={disabled}>
            {/* Model editors stay mounted while hidden so switching tabs keeps their unsaved buffers. */}
            <div hidden={section !== "models" || modelTab !== "connections"}>
              <ConnectionSettings key={snapshotVersion} savedDraft={savedDraft} catalogs={catalogs} usage={usage} api={api} persist={persist} reportDirty={reportDirty} />
            </div>
            <div hidden={section !== "models" || modelTab !== "groups"}>
              <GroupSettings key={snapshotVersion} savedDraft={savedDraft} usage={usage} persist={persist} reportDirty={reportDirty} />
            </div>
            {section === "workspace" && <WorkspaceSettings fields={fields} setField={setField} dirty={workspaceDirty} onSave={() => saveFields(FIELD_SECTIONS.workspace, { workspace: true })} />}
            {section === "runtime" && <RuntimeSettings fields={fields} setField={setField} dirty={runtimeDirty} onSave={() => saveFields(FIELD_SECTIONS.runtime)} />}
          </fieldset>
        </div>
      </div>

      <footer className="settings-actions">
        <div className="settings-save-state" aria-live="polite">
          {confirmClose
            ? <><strong>有尚未保存的修改</strong><span>关闭后，本次修改将被丢弃。</span></>
            : <><strong>{disabled ? "正在应用设置" : pending.length ? `有未保存的修改（${pending.join("、")}）` : "所有修改已保存"}</strong><span>{sectionError || (pending.length ? "请在对应编辑区点击保存。" : "新运行将使用最新配置，正在运行的任务保留原配置。")}</span></>}
        </div>
        {confirmClose && <button type="button" className="plain-action" onClick={() => setConfirmClose(false)}>继续编辑</button>}
        {confirmClose && <button type="button" className="plain-action settings-discard" onClick={onClose}>放弃修改</button>}
        {!confirmClose && <button className="plain-action" disabled={disabled} type="button" onClick={requestClose}>返回工作区</button>}
      </footer>
    </section>
  </div>;
}
