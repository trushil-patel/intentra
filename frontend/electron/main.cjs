const { app, BrowserWindow, ipcMain, Menu, screen } = require("electron");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { AgentController } = require("./controller.cjs");
const root = path.resolve(__dirname, "../..");
let main,
  overlay,
  controller,
  quitting = false;
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    main?.show();
    main?.focus();
  });
  app.whenReady().then(() => {
    controller = new AgentController(root);
    Menu.setApplicationMenu(
      Menu.buildFromTemplate([
        {
          label: "Intentra",
          submenu: [
            { label: "Command center", click: () => main.show() },
            { label: "Show floating assistant", click: () => showOverlay() },
            { label: "Hide floating assistant", click: () => overlay.hide() },
            { type: "separator" },
            { label: "Quit Intentra", click: () => app.quit() },
          ],
        },
      ]),
    );
    const entry = path.join(__dirname, "../dist/index.html");
    function create(compact) {
      const win = new BrowserWindow({
        width: compact ? 390 : 1240,
        height: compact ? 280 : 850,
        minWidth: compact ? 390 : 760,
        minHeight: compact ? 200 : 650,
        show: false,
        frame: !compact,
        alwaysOnTop: compact,
        focusable: !compact,
        resizable: !compact,
        skipTaskbar: compact,
        backgroundColor: "#080B11",
        title: "Intentra OS",
        webPreferences: {
          preload: path.join(__dirname, "preload.cjs"),
          nodeIntegration: false,
          contextIsolation: true,
          sandbox: true,
          webSecurity: true,
        },
      });
      win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
      win.webContents.on("will-navigate", (e) => e.preventDefault());
      win.webContents.session.setPermissionRequestHandler((_w, _p, cb) =>
        cb(false),
      );
      win.webContents.session.setPermissionCheckHandler(() => false);
      win.loadFile(entry, { query: compact ? { overlay: "1" } : {} });
      win.on("close", (e) => {
        if (compact && !quitting) {
          e.preventDefault();
          win.hide();
        }
      });
      return win;
    }
    main = create(false);
    overlay = create(true);
    main.once("ready-to-show", () => main.show());
    main.on("closed", () => {
      main = null;
      if (!quitting) app.quit();
    });
    const allowed = new Set([
      "start",
      "stop",
      "overlay",
      "hide-overlay",
      "expand",
      "collapse",
      "dashboard",
      "clear-history",
      "quit",
    ]);
    const trusted = (event) =>
      [main, overlay].some(
        (w) =>
          w &&
          !w.isDestroyed() &&
          w.webContents === event.sender &&
          event.senderFrame === w.webContents.mainFrame &&
          event.senderFrame.url.startsWith(pathToFileURL(entry).href),
      );
    ipcMain.handle("intentra:snapshot", (e) => {
      if (!trusted(e)) throw Error("Untrusted sender");
      return controller.snapshot();
    });
    ipcMain.handle("intentra:action", (e, action) => {
      if (!trusted(e) || !allowed.has(action))
        throw Error("Unsupported action");
      if (action === "start") controller.start();
      if (action === "stop") controller.send("stop");
      if (action === "clear-history") controller.clearHistory();
      if (action === "overlay") showOverlay();
      if (action === "hide-overlay") overlay.hide();
      if (action === "expand") overlay.setSize(390, 440);
      if (action === "collapse") overlay.setSize(390, 280);
      if (action === "dashboard") {
        main.show();
        main.focus();
      }
      if (action === "quit") app.quit();
      return controller.snapshot();
    });
    controller.on("snapshot", (value) => {
      for (const w of [main, overlay])
        if (w && !w.isDestroyed())
          w.webContents.send("intentra:snapshot", value);
    });
    screen.on("display-removed", () => placeOverlay());
    screen.on("display-metrics-changed", () => placeOverlay());
  });
}
function placeOverlay() {
  if (!overlay || overlay.isDestroyed()) return;
  const a = screen.getDisplayNearestPoint(
    screen.getCursorScreenPoint(),
  ).workArea;
  const [w, h] = overlay.getSize();
  overlay.setPosition(
    Math.round(a.x + Math.max(0, a.width - w - 24)),
    Math.round(a.y + Math.max(0, a.height - h - 24)),
  );
}
function showOverlay() {
  placeOverlay();
  overlay.showInactive();
}
app.on("before-quit", (e) => {
  if (!quitting) {
    e.preventDefault();
    quitting = true;
    Promise.resolve(controller?.close()).finally(() => app.quit());
  }
});
app.on("window-all-closed", () => app.quit());
