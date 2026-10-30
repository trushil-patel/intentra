const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld(
  "intentra",
  Object.freeze({
    snapshot: () => ipcRenderer.invoke("intentra:snapshot"),
    action: (action) => ipcRenderer.invoke("intentra:action", action),
    subscribe: (callback) => {
      const listener = (_event, value) => callback(value);
      ipcRenderer.on("intentra:snapshot", listener);
      return () => ipcRenderer.removeListener("intentra:snapshot", listener);
    },
  }),
);
