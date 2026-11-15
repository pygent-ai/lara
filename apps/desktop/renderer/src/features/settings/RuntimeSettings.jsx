import React from "react";
import { SectionHead } from "./SectionHead.jsx";

export function RuntimeSettings({ fields, setField, dirty, onSave }) {
  const retry = fields.retry;
  return <section className="settings-card" aria-label="运行与权限">
    <SectionHead title="运行参数" dirty={dirty} onSave={onSave} saveLabel="保存运行与权限" />
    <div className="settings-card-body">
      <div className="field-grid">
        <label>
          <span>最大执行步数</span>
          <input type="number" value={fields.maxSteps} onChange={(event) => setField("maxSteps", Number(event.target.value))} />
          <small>-1 表示不限制。</small>
        </label>
        <label>
          <span>上下文窗口（tokens）</span>
          <input min="1" placeholder="留空使用模型默认值" type="number" value={fields.contextWindow} onChange={(event) => setField("contextWindow", event.target.value)} />
        </label>
      </div>
      <label>
        <span>工具权限</span>
        <select value={fields.approvalsEnabled ? "approval" : "full-access"} aria-describedby="tool-permissions-help" onChange={(event) => setField("approvalsEnabled", event.target.value === "approval")}>
          <option value="approval">逐次审批</option>
          <option value="full-access">完全访问</option>
        </select>
        <small id="tool-permissions-help">完全访问会自动允许所有工具调用；逐次审批会要求确认写入和外部操作（已预授权的工具除外）。保存后对所有工作区的新运行生效。</small>
      </label>
      <h5>模型重试与切换</h5>
      <p className="field-note">所有模型组共用这组参数：一个模型连续失败达到次数后，切换到模型组里的下一个模型。</p>
      <div className="field-grid">
        <label>
          <span>每个模型尝试次数</span>
          <input min="1" type="number" value={retry.max_attempts_per_model} onChange={(event) => setField("retry", { ...retry, max_attempts_per_model: event.target.value })} />
        </label>
        <label>
          <span>空闲超时（秒）</span>
          <input min="0.1" step="0.1" type="number" value={retry.attempt_idle_timeout_seconds} onChange={(event) => setField("retry", { ...retry, attempt_idle_timeout_seconds: event.target.value })} />
        </label>
      </div>
    </div>
  </section>;
}
