const BADGE_DATA_URL_PREFIX = "data:image/png;base64,";

// Windows shows the taskbar badge through a 16x16 overlay icon drawn by the
// renderer. Electron has no numeric setBadgeCount on win32, so the renderer
// passes a PNG data URL plus the real count for the accessibility label.
export function applyBadgeOverlay({ window, count, dataUrl, createImage, platform }) {
  if (platform && platform !== "win32") {
    return false;
  }
  if (!window || typeof window.setOverlayIcon !== "function" || window.isDestroyed?.()) {
    return false;
  }
  const badgeCount = normalizeBadgeCount(count);
  if (badgeCount <= 0) {
    window.setOverlayIcon(null, "");
    return true;
  }
  if (typeof dataUrl !== "string" || !dataUrl.startsWith(BADGE_DATA_URL_PREFIX)) {
    return false;
  }
  const icon = createImage(dataUrl);
  if (!icon || icon.isEmpty()) {
    return false;
  }
  window.setOverlayIcon(icon, `有 ${badgeCount} 个会话已完成，待查看`);
  return true;
}

function normalizeBadgeCount(count) {
  const value = Number(count);
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}
