import React from "react";

export function WorkspaceSettings({ draft, setField, activeSection, onSave, sectionDirty }) {
  return <>
          <section className="settings-disclosure settings-workspace" hidden={activeSection !== "workspace"}>
          <div className="section-save-row">
          <h4>项目默认值</h4>
          {sectionDirty && <span className="connection-editor-dirty">未保存</span>}
          <button type="button" className="route-add section-save" disabled={!sectionDirty} onClick={onSave} aria-label="保存项目与 Agent">保存</button>
          </div>
          <div className="settings-disclosure-body">
          <label>
            <span>工作目录</span>
            <input value={draft.workspaceRoot} onChange={(event) => setField("workspaceRoot", event.target.value)} />
          </label>
          <label>
            <span>Agent</span>
            <input value={draft.agent} onChange={(event) => setField("agent", event.target.value)} />
          </label>
          </div>
          </section>
  </>;
}
