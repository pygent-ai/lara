import React from "react";
import { SectionHead } from "./SectionHead.jsx";

export function WorkspaceSettings({ fields, setField, dirty, onSave }) {
  return <section className="settings-card" aria-label="项目与 Agent">
    <SectionHead title="项目默认值" dirty={dirty} onSave={onSave} saveLabel="保存项目与 Agent" />
    <div className="settings-card-body">
      <label>
        <span>工作目录</span>
        <input value={fields.workspaceRoot} onChange={(event) => setField("workspaceRoot", event.target.value)} />
      </label>
      <label>
        <span>Agent</span>
        <input value={fields.agent} onChange={(event) => setField("agent", event.target.value)} />
        <small>切换工作目录时，未修改的 Agent 会重置为新项目的默认值。</small>
      </label>
    </div>
  </section>;
}
