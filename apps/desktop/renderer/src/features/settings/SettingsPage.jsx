import React, { useEffect, useRef, useState } from "react";
import { ArrowLeft, Settings as SettingsIcon } from "lucide-react";
import { useSettingsDraft } from "./useSettingsDraft.js";
import { commitConnectionBuffer, commitConnectionFields, commitModelDelete, commitModelSave, modelGroupValidationError, modelKeyRenamesForSave, settingsForSave, settingsToDraft } from "./settingsModel.js";
import "./settings.css";
import { WorkspaceSettings } from "./WorkspaceSettings.jsx";
import { ConnectionSettings } from "./ConnectionSettings.jsx";
import { GroupSettings } from "./GroupSettings.jsx";
import { RuntimeSettings } from "./RuntimeSettings.jsx";

const SECTION_FIELDS = {
  groups: ["modelGroups", "defaultModelGroup", "retry"],
  workspace: ["workspaceRoot", "agent"],
  runtime: ["maxSteps", "contextWindow", "approvalsEnabled"],
};

export function SettingsPage({ settings, disabled, onClose, onPersist, api }) {

  const [activeSection, setActiveSection] = useState("model-config");
  const [activeModelTab, setActiveModelTab] = useState("pool");
  const [confirmClose, setConfirmClose] = useState(false);
  // Unsaved edits inside an open connection editor; they live in the editor
  // buffer and are invisible to the draft-vs-saved comparison.
  const [connectionsDirty, setConnectionsDirty] = useState(false);
  const controller = useSettingsDraft(settings, api, disabled);
  const { snapshot, draft, setDraft } = controller;
  const settingsRef = useRef(null);
  // The last persisted draft, re-based on the page's own snapshot (fetched on
  // mount). Connection saves update this (plus the draft) per section, so
  // unsaved edits in other sections always survive.
  const [savedDraft, setSavedDraft] = useState(() => settingsToDraft(snapshot));
  useEffect(() => { setSavedDraft(settingsToDraft(snapshot)); }, [snapshot]);
  // Every tab saves its own slice: connection edits live in editor buffers,
  // the other tabs commit their draft fields over the saved baseline.
  const fieldsDirty = (fields) => fields.some((field) => JSON.stringify(draft[field]) !== JSON.stringify(savedDraft[field]));
  const groupsDirty = fieldsDirty(SECTION_FIELDS.groups);
  const workspaceDirty = fieldsDirty(SECTION_FIELDS.workspace);
  const runtimeDirty = fieldsDirty(SECTION_FIELDS.runtime);
  const dirty = connectionsDirty || groupsDirty || workspaceDirty || runtimeDirty;
  const sections = [
    ["model-config", "模型配置", "连接、模型与模型组"],
    ["workspace", "项目与 Agent", "工作目录与执行角色"],
    ["runtime", "运行与权限", "执行步数、上下文与审批"],
  ];
  const modelTabs = [
    ["pool", "模型资源池"],
    ["groups", "模型组"],
  ];
  const currentSection = sections.find(([id]) => id === activeSection);
  function requestClose() {
    if (disabled) return;
    if (dirty) setConfirmClose(true);
    else onClose();
  }
  function navigateSection(id) {
    setActiveSection(id);
    setConfirmClose(false);
    settingsRef.current?.querySelector(".settings-form")?.scrollTo(0, 0);
  }
  const missingCredential = Object.entries(draft.connections).find(([, connection]) => !connection.credential?.none && connection.credential_source === "missing" && !draft.credentialValues[connection.credential?.env]?.trim());
  const validationError = modelGroupValidationError(draft) || (missingCredential ? `连接 ${missingCredential[0]} 缺少 API Key，请在简洁配置中填写。` : "");
  const validationSection = "model-config";
  const [sectionSaveError, setSectionSaveError] = useState("");
  const pendingSections = [
    connectionsDirty && "连接",
    groupsDirty && "模型组",
    workspaceDirty && "项目",
    runtimeDirty && "运行",
  ].filter(Boolean);
  const saveHint = pendingSections.length ? "请点击对应页签内的保存按钮。" : "配置完整。";

  async function persistSection(fields) {
    const nextSaved = { ...savedDraft };
    for (const field of fields) nextSaved[field] = draft[field];
    await onPersist(nextSaved);
    setSavedDraft(nextSaved);
  }

  async function runSectionSave(save) {
    setSectionSaveError("");
    try {
      await save();
    } catch (error) {
      setSectionSaveError(typeof error?.message === "string" && error.message ? error.message : "保存失败，请稍后再试。");
    }
  }

  function saveGroups() {
    const error = modelGroupValidationError(draft);
    if (error) {
      setSectionSaveError(error);
      return Promise.resolve();
    }
    return persistSection(SECTION_FIELDS.groups);
  }

  function saveWorkspace() {
    // Keep the workspace-switch rule: a changed workspace resets an unchanged agent.
    const merged = { ...savedDraft, workspaceRoot: draft.workspaceRoot, agent: draft.agent };
    const nextSaved = settingsForSave(merged, { workspace_root: savedDraft.workspaceRoot, agent: savedDraft.agent });
    const workspaceChanged = nextSaved.workspaceRoot.trim() !== savedDraft.workspaceRoot.trim();
    return onPersist(nextSaved, { workspaceChanged }).then(() => {
      setSavedDraft(nextSaved);
      setDraft((current) => ({ ...current, agent: nextSaved.agent }));
    });
  }

  function saveRuntime() {
    return persistSection(SECTION_FIELDS.runtime);
  }

  // A connection save persists the saved baseline plus this connection's slice,
  // and mirrors the same commit into the live draft so pending edits in other
  // sections (workspace, groups) survive untouched.
  // A connection editor commits in slices: the create form and the head save
  // cover the whole slice or the connection fields, and every model row
  // commits (or deletes) on its own. Both the saved baseline and the live
  // draft receive the same merge, so pending edits elsewhere survive.
  async function persistConnection(previousKey, buffer, catalogs, kind = "slice", modelKey = null) {
    const merge = (base) => (
      kind === "model" ? commitModelSave(base, previousKey, buffer, catalogs, modelKey)
        : kind === "delete" ? commitModelDelete(base, previousKey, buffer, catalogs, modelKey)
          : kind === "connection" ? commitConnectionFields(base, previousKey, buffer, catalogs)
            : commitConnectionBuffer(base, previousKey, buffer, catalogs)
    );
    const nextSaved = merge(savedDraft);
    // A row save that renames a model tells the backend so it can migrate the
    // sessions still pointing at the old key instead of rejecting the save.
    const modelKeyRenames = kind === "model"
      ? modelKeyRenamesForSave(savedDraft, buffer, modelKey)
      : undefined;
    await onPersist(nextSaved, { modelKeyRenames });
    setSavedDraft(nextSaved);
    setDraft(merge(draft));
  }

  useEffect(() => {
    const previous = document.activeElement;
    settingsRef.current?.querySelector("button")?.focus();
    return () => previous?.focus?.();
  }, []);

  return (
    <div className="settings-page">
      <section className="settings-panel" aria-label="Settings" role="region" ref={settingsRef} onKeyDown={(event) => {
        if (event.key === "Escape") { event.stopPropagation(); confirmClose ? setConfirmClose(false) : requestClose(); }

      }}>
        <header className="settings-header">
          <div>
            <h2>设置</h2>
          </div>
          <button className="plain-action settings-back" type="button" disabled={disabled} onClick={requestClose} aria-label="Close settings" title="Close settings">
            <ArrowLeft size={18} aria-hidden="true" /> 返回工作区
          </button>
        </header>

        <div className="settings-layout">
          <nav className="settings-nav" aria-label="配置分类">
            <span className="settings-nav-label">工作区设置</span>
            {sections.map(([id, title, description, count], index) => <button type="button" key={id} aria-current={activeSection === id ? "page" : undefined} onClick={() => navigateSection(id)}>
              <span className="settings-nav-icon">{index < 3 ? String(index + 1).padStart(2, "0") : <SettingsIcon size={16} />}</span>
              <span><strong>{title}</strong><small>{description}</small></span>
              {count !== undefined && <span className="settings-nav-count">{count}</span>}
            </button>)}
          </nav>
          <div className="settings-form">
            <div className="settings-page-heading"><h3>{currentSection[1]}</h3></div>
            {activeSection === "model-config" && <nav className="settings-subtabs" aria-label="模型配置分层">
              {modelTabs.map(([id, title]) => <button type="button" key={id} aria-current={activeModelTab === id ? "true" : undefined} onClick={() => setActiveModelTab(id)}>{title}</button>)}
            </nav>}
            <fieldset className="settings-fields" disabled={disabled}>
          <WorkspaceSettings {...controller} activeSection={activeSection} onSave={() => runSectionSave(saveWorkspace)} sectionDirty={workspaceDirty} />
          <ConnectionSettings {...controller} savedDraft={savedDraft} activeSection={activeSection} activeModelTab={activeModelTab} onPersist={persistConnection} onDirtyChange={setConnectionsDirty} />
          <GroupSettings {...controller} activeSection={activeSection} activeModelTab={activeModelTab} onSave={() => runSectionSave(saveGroups)} sectionDirty={groupsDirty} />
          <RuntimeSettings {...controller} activeSection={activeSection} onSave={() => runSectionSave(saveRuntime)} sectionDirty={runtimeDirty} />
            </fieldset>
          </div>
        </div>

        <footer className="settings-actions">
          <div className="settings-save-state" aria-live="polite">
            {confirmClose ? <><strong>有尚未保存的修改</strong><span>关闭后，本次修改将被丢弃。</span></> : <><strong>{disabled ? "正在应用设置" : pendingSections.length ? `有未保存的修改（${pendingSections.join("、")}）` : "尚未修改"}</strong><span>{sectionSaveError || saveHint}</span></>}
          </div>
          {!confirmClose && validationError && activeSection !== validationSection && <button type="button" className="plain-action" onClick={() => navigateSection(validationSection)}>前往完善</button>}
          {confirmClose && <button type="button" className="plain-action" onClick={() => setConfirmClose(false)}>继续编辑</button>}
          {confirmClose && <button type="button" className="plain-action settings-discard" onClick={onClose}>放弃修改</button>}
          <button hidden={confirmClose} className="plain-action" disabled={disabled} type="button" onClick={requestClose}>
            返回工作区
          </button>
        </footer>
      </section>
    </div>
  );
}
