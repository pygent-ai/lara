// A dead renderer paints as a silent white screen, and without these handlers
// the terminal only shows a bare crashpad "not connected" line. Surface death
// reasons (and, in dev, renderer console errors) on stderr next to Vite output,
// and reload an abnormally-dead renderer so a single crash heals itself.
export function attachProcessDiagnostics({ app, logger = console }) {
  app.on("child-process-gone", (_event, details) => {
    logger.error(
      `[lara] ${details.type} process gone: reason=${details.reason} exitCode=${details.exitCode} name=${details.name}`,
    );
  });
}

// Chromium's ERR_ABORTED: navigations superseded before commit also land here.
const ERR_ABORTED = -3;

// A crashed renderer never recovers on its own: the window stays white while
// the backend keeps running. Reload once per death so a single OOM or crash
// heals silently, but stop after repeated deaths inside a short window — a
// crash loop must surface in the logs, not spin-reload forever.
const RELOAD_DELAY_MS = 1_000;
const RELOAD_LIMIT = 3;
const RELOAD_WINDOW_MS = 60_000;

export function attachWindowDiagnostics({ window, logger = console, isPackaged = false }) {
  const webContents = window.webContents;
  let reloadTimestamps = [];
  let autoReloadDisabled = false;

  webContents.on("render-process-gone", (_event, details) => {
    // A normal window close also reports the renderer leaving with clean-exit.
    if (details.reason === "clean-exit") {
      return;
    }
    logger.error(`[lara] renderer gone: reason=${details.reason} exitCode=${details.exitCode}`);
    if (autoReloadDisabled) {
      return;
    }
    const now = Date.now();
    reloadTimestamps = reloadTimestamps.filter((at) => now - at < RELOAD_WINDOW_MS);
    if (reloadTimestamps.length >= RELOAD_LIMIT) {
      autoReloadDisabled = true;
      logger.error(`[lara] renderer crashed ${RELOAD_LIMIT} times within ${RELOAD_WINDOW_MS / 1000}s; auto-reload disabled`);
      return;
    }
    reloadTimestamps.push(now);
    setTimeout(() => {
      if (!webContents.isDestroyed()) {
        webContents.reload();
      }
    }, RELOAD_DELAY_MS);
  });

  webContents.on("unresponsive", () => {
    logger.error("[lara] renderer unresponsive");
  });

  webContents.on("did-fail-load", (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
    if (!isMainFrame || errorCode === ERR_ABORTED) {
      return;
    }
    logger.error(`[lara] main frame failed to load: ${errorDescription} (${errorCode}) ${validatedURL}`);
  });

  if (isPackaged) {
    return;
  }

  webContents.on("console-message", (details) => {
    if (details.level !== "error") {
      return;
    }
    logger.error(`[lara] renderer error: ${details.message} (${details.sourceId}:${details.lineNumber})`);
  });
}
