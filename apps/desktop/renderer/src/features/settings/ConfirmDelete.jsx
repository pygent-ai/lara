import React from "react";

// Inline confirmation for destructive actions. It renders where the action
// was triggered so the user sees what the deletion affects (session impact
// comes from the backend usage counts) before committing.
export function ConfirmDelete({ title, impact, busy, error, onConfirm, onCancel }) {
  return <div className="confirm-delete" role="alertdialog" aria-label={title}>
    <strong>{title}</strong>
    {impact && <p>{impact}</p>}
    {error && <p className="confirm-delete-error" role="alert">{error}</p>}
    <div className="confirm-delete-actions">
      <button type="button" className="plain-action" disabled={busy} onClick={onCancel}>取消</button>
      <button type="button" className="danger-action" disabled={busy} onClick={onConfirm}>{busy ? "删除中…" : "确认删除"}</button>
    </div>
  </div>;
}

export function errorText(error) {
  return typeof error?.message === "string" && error.message ? error.message : "保存失败，请稍后再试。";
}
