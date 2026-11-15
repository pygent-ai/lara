import React from "react";

// Heading row shared by every editor and section: title, an unsaved marker
// and the one save button that commits this entity.
export function SectionHead({ title, description, dirty, onSave, saveLabel, saveDisabled = false, saveTitle = "", children }) {
  return <div className="section-head">
    <div className="section-head-copy">
      <h4>{title}</h4>
      {description && <p>{description}</p>}
    </div>
    {children}
    {dirty && <span className="unsaved-chip">未保存</span>}
    {onSave && <button type="button" className="primary-action" disabled={!dirty || saveDisabled} title={saveTitle || undefined} onClick={onSave} aria-label={saveLabel}>保存</button>}
  </div>;
}
