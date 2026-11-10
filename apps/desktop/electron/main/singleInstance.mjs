export function registerSingleInstance({ app, getWindow }) {
  if (!app.requestSingleInstanceLock()) {
    app.quit();
    return false;
  }

  app.on("second-instance", () => {
    const window = getWindow();
    if (!window) {
      return;
    }
    if (window.isMinimized()) {
      window.restore();
    }
    window.show();
    window.focus();
  });
  return true;
}

export function registerDevParentExit({ app, processRef = process }) {
  if (app.isPackaged || !processRef.connected) {
    return false;
  }

  processRef.once("disconnect", () => app.quit());
  return true;
}
