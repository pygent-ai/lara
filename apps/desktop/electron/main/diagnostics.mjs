// A dead renderer paints as a silent white screen, and without these handlers
// the terminal only shows a bare crashpad "not connected" line. Surface death
// reasons (and, in dev, renderer console errors) on stderr next to Vite output.
export function attachProcessDiagnostics({ app, logger = console }) {
  app.on("child-process-gone", (_event, details) => {
    logger.error(
      `[lara] ${details.type} process gone: reason=${details.reason} exitCode=${details.exitCode} name=${details.name}`,
    );
  });
}

// Chromium's ERR_ABORTED: navigations superseded before commit also land here.
const ERR_ABORTED = -3;

export function attachWindowDiagnostics({ window, logger = console, isPackaged = false }) {
  const webContents = window.webContents;

  webContents.on("render-process-gone", (_event, details) => {
    // A normal window close also reports the renderer leaving with clean-exit.
    if (details.reason === "clean-exit") {
      return;
    }
    logger.error(`[lara] renderer gone: reason=${details.reason} exitCode=${details.exitCode}`);
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
