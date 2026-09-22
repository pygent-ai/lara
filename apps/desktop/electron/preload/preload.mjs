import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("__LARA_API_BASE_URL__", process.env.LARA_API_BASE_URL || "http://127.0.0.1:8765");

contextBridge.exposeInMainWorld("laraDesktop", {
  chooseProjectDirectory: (defaultPath) => ipcRenderer.invoke("project:choose-directory", defaultPath),
  getBackendStatus: () => ipcRenderer.invoke("backend:status"),
});
