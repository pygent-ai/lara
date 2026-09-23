import { sessionStatusIdentity, sessionStatusKind } from "./sessionStatusState.js";

export const BADGE_ICON_SIZE = 16;
export const BADGE_MAX_COUNT = 9;

// A session is badge-worthy once a run finished and the user has not opened
// that result yet; sessions that never ran have nothing "completed" to report.
export function isUnacknowledgedCompletedSession(session, acknowledgedStatuses) {
  const status = String(session?.last_case_run_status || "");
  if (!status || sessionStatusKind(status) === "running") {
    return false;
  }
  const sessionId = String(session?.session_id || "");
  return acknowledgedStatuses?.[sessionId] !== sessionStatusIdentity(session);
}

export function countUnacknowledgedCompletedSessions(sessionGroups, acknowledgedStatuses) {
  let count = 0;
  for (const group of Array.isArray(sessionGroups) ? sessionGroups : []) {
    for (const session of group?.sessions || []) {
      if (isUnacknowledgedCompletedSession(session, acknowledgedStatuses)) {
        count += 1;
      }
    }
  }
  return count;
}

// Windows renders the taskbar badge as a 16x16 overlay icon, so the number has
// to be drawn in the renderer (the only process with a canvas) and shipped to
// the main process as a PNG data URL.
export function renderBadgeDataUrl(count, doc = globalThis.document) {
  const total = Math.floor(Number(count) || 0);
  if (total <= 0 || !doc) {
    return null;
  }
  const canvas = doc.createElement("canvas");
  canvas.width = BADGE_ICON_SIZE;
  canvas.height = BADGE_ICON_SIZE;
  const context = canvas.getContext("2d");
  if (!context) {
    return null;
  }
  const label = total > BADGE_MAX_COUNT ? `${BADGE_MAX_COUNT}+` : String(total);
  context.beginPath();
  context.arc(BADGE_ICON_SIZE / 2, BADGE_ICON_SIZE / 2, BADGE_ICON_SIZE / 2, 0, Math.PI * 2);
  context.fillStyle = "#d92d20";
  context.fill();
  context.fillStyle = "#ffffff";
  context.font = `bold ${label.length > 1 ? 9 : 12}px sans-serif`;
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillText(label, BADGE_ICON_SIZE / 2, BADGE_ICON_SIZE / 2 + 0.5);
  return canvas.toDataURL("image/png");
}

export function updateTaskbarBadge(count, desktop = globalThis.window?.laraDesktop, doc = globalThis.document) {
  if (typeof desktop?.setBadgeCount !== "function") {
    return false;
  }
  const total = Math.max(0, Math.floor(Number(count) || 0));
  const dataUrl = total > 0 ? renderBadgeDataUrl(total, doc) : null;
  if (total > 0 && !dataUrl) {
    return false;
  }
  // Badge updates are optional UI state; IPC failures must not break the UI.
  Promise.resolve(desktop.setBadgeCount(total, dataUrl)).catch(() => {});
  return true;
}
