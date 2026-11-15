import React, { useEffect, useState } from "react";
import { CalendarClock, Check, Pause, Play, Plus, RefreshCw, Trash2, X } from "lucide-react";

// The page is a view over the SDK scheduler for the active workspace: tasks
// carry a schedule expression (every <n>[smhd] | at <ISO> | daily HH:MM | on
// <event>), writes are signed submissions the daemon promotes on its next
// poll, and every task runs as a turn in its session.
const EMPTY = {
  name: "",
  prompt: "",
  instructions: "",
  session: "",
  priority: "normal",
  schedule_type: "daily",
  time: "09:00",
  every: "1h",
  at_time: "",
  event: "",
};

const STATUS_LABELS = {
  approved: "已排期",
  needs_review: "待审批",
  paused: "已暂停",
  rejected: "已拒绝",
};

export function ScheduledPage({ api, onOpenSession }) {
  const [items, setItems] = useState([]);
  const [runs, setRuns] = useState({});
  const [status, setStatus] = useState("");
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function refresh() {
    const result = await api.listScheduledTasks(status);
    setItems(result.tasks || []);
  }

  useEffect(() => { refresh().catch((err) => setError(String(err.message || err))); }, [status]);

  async function submit(operation, message) {
    setBusy(true);
    setError("");
    try {
      await operation();
      // Submissions take effect on the daemon's next poll; the list refreshes
      // then, so tell the user instead of pretending the row already changed.
      setNotice(message);
      await refresh();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy(false);
    }
  }

  async function save(event) {
    event.preventDefault();
    await submit(() => api.createScheduledTask(taskPayload(form)), "任务已提交，调度器下次轮询后出现在列表中。");
    setEditing(false);
  }

  async function toggleRuns(item) {
    if (runs[item.id]) {
      setRuns((current) => ({ ...current, [item.id]: null }));
      return;
    }
    try {
      const result = await api.getScheduledTask(item.id);
      setRuns((current) => ({ ...current, [item.id]: result.runs || [] }));
    } catch (err) { setError(String(err.message || err)); }
  }

  return <section className="scheduled-page" aria-label="定时任务">
    <header className="scheduled-header">
      <div><span className="scheduled-eyebrow">SCHEDULER</span><h2>定时任务</h2><p>让 Lara 在后台按计划或事件执行任务，每次运行是任务会话里的一轮。</p></div>
      <button className="send scheduled-create" type="button" onClick={() => { setForm(EMPTY); setEditing(true); }}><Plus size={16} />新建</button>
    </header>
    <div className="scheduled-filters">
      {[['', '全部'], ['approved', '已排期'], ['needs_review', '待审批'], ['paused', '已暂停']].map(([value, label]) =>
        <button className={status === value ? "active" : ""} type="button" key={value} onClick={() => setStatus(value)}>{label}</button>)}
      <button type="button" title="刷新" onClick={() => refresh()}><RefreshCw size={14} /></button>
    </div>
    {error && <div className="scheduled-error" role="alert">{error}</div>}
    {notice && <div className="scheduled-notice" role="status">{notice}</div>}
    <div className="scheduled-list">
      {!items.length && <div className="scheduled-empty"><CalendarClock size={32} /><strong>还没有定时任务</strong><span>新建一个任务，或在对话中让 Lara 用 schedule_task 创建（agent 创建的任务需要在这里审批）。</span></div>}
      {items.map((item) => <article className="automation-card" key={item.id}>
        <div className="automation-card-main">
          <div className="automation-icon"><CalendarClock size={17} /></div>
          <div><h3>{item.name}</h3><p>{item.prompt}</p><div className="automation-meta"><span>{item.schedule}</span><span>下次 {formatDate(item.next_run_at)}</span><span>会话 {item.session}</span>{item.origin !== "user" && <span>来自 {item.origin}</span>}</div></div>
          <span className={`automation-status ${item.status}`}>{STATUS_LABELS[item.status] || item.status}</span>
        </div>
        <div className="automation-actions">
          {item.status === "needs_review" && <button type="button" disabled={busy} onClick={() => submit(() => api.controlScheduledTask(item.id, "approve"), "已提交审批。")}><Check size={14} />批准</button>}
          {item.status === "needs_review" && <button type="button" disabled={busy} onClick={() => submit(() => api.controlScheduledTask(item.id, "reject"), "已提交拒绝。")}>拒绝</button>}
          {item.status === "approved" && <button type="button" disabled={busy} onClick={() => submit(() => api.controlScheduledTask(item.id, "pause"), "已提交暂停。")}><Pause size={14} />暂停</button>}
          {item.status === "paused" && <button type="button" disabled={busy} onClick={() => submit(() => api.controlScheduledTask(item.id, "resume"), "已提交恢复。")}><Play size={14} />恢复</button>}
          <button type="button" onClick={() => toggleRuns(item)}>运行记录</button>
          <button className="danger" type="button" disabled={busy} onClick={() => submit(() => api.controlScheduledTask(item.id, "delete"), "已提交删除。")}><Trash2 size={14} /></button>
        </div>
        {runs[item.id] && <div className="automation-runs">{runs[item.id].length === 0 ? <span>暂无运行记录</span> : runs[item.id].map((run) =>
          <div className="automation-run" key={run.id}>
            <button className="automation-run-open" type="button" onClick={() => onOpenSession(item.session)}>
              <span className={`run-dot ${run.status}`} /><strong>{run.status}</strong><span>{formatDate(run.started_at)}</span>{run.missed && <span>补跑</span>}<span>{run.error || run.output || ""}</span>
            </button>
          </div>)}</div>}
      </article>)}
    </div>
    {editing && <div className="automation-editor-backdrop"><form className="automation-editor" onSubmit={save}>
      <header><div><span className="scheduled-eyebrow">NEW TASK</span><h3>新建定时任务</h3></div><button type="button" onClick={() => setEditing(false)}><X /></button></header>
      <label>名称<input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
      <label>任务提示词<textarea required rows="6" value={form.prompt} onChange={(event) => setForm({ ...form, prompt: event.target.value })} /></label>
      <label>补充说明（可选）<textarea rows="2" value={form.instructions} onChange={(event) => setForm({ ...form, instructions: event.target.value })} /></label>
      <div className="automation-form-row">
        <label>会话<input placeholder="留空使用调度器默认会话" value={form.session} onChange={(event) => setForm({ ...form, session: event.target.value })} /></label>
        <label>优先级<select value={form.priority} onChange={(event) => setForm({ ...form, priority: event.target.value })}><option value="high">高</option><option value="normal">普通</option><option value="low">低</option></select></label>
      </div>
      <label>频率<select value={form.schedule_type} onChange={(event) => setForm({ ...form, schedule_type: event.target.value })}><option value="once">单次</option><option value="every">固定间隔</option><option value="daily">每天</option><option value="event">事件触发</option></select></label>
      {form.schedule_type === "once" && <label>执行时间<input required type="datetime-local" value={form.at_time} onChange={(event) => setForm({ ...form, at_time: event.target.value })} /></label>}
      {form.schedule_type === "every" && <label>间隔<input required placeholder="30m / 2h / 1d" value={form.every} onChange={(event) => setForm({ ...form, every: event.target.value })} /></label>}
      {form.schedule_type === "daily" && <label>时间<input type="time" required value={form.time} onChange={(event) => setForm({ ...form, time: event.target.value })} /></label>}
      {form.schedule_type === "event" && <label>事件名<input required placeholder="deploy-finished" value={form.event} onChange={(event) => setForm({ ...form, event: event.target.value })} /></label>}
      <footer><button type="button" onClick={() => setEditing(false)}>取消</button><button className="send" disabled={busy} type="submit">提交</button></footer>
    </form></div>}
  </section>;
}

export function taskPayload(form) {
  const payload = {
    name: form.name.trim(),
    prompt: form.prompt.trim(),
    instructions: form.instructions.trim(),
    priority: form.priority,
    schedule: scheduleExpression(form),
  };
  if (form.session.trim()) payload.session = form.session.trim();
  return payload;
}

export function scheduleExpression(form) {
  if (form.schedule_type === "once") return `at ${new Date(form.at_time).toISOString()}`;
  if (form.schedule_type === "every") return `every ${form.every.trim()}`;
  if (form.schedule_type === "event") return `on ${form.event.trim()}`;
  return `daily ${form.time}`;
}

function formatDate(value) { return value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—"; }
