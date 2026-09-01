const { app, BrowserWindow, Menu } = require("electron");
const { randomBytes } = require("node:crypto");
const path = require("node:path");
const { createBackendController, resolveBackendLaunch } = require("./backend_lifecycle.cjs");

const projectRoot = path.resolve(__dirname, "..");
const wsPort = 8765;
let quitAfterBackend = false;
const shutdownToken = randomBytes(32).toString("hex");

const backendController = createBackendController({
  ...resolveBackendLaunch({
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    projectRoot,
    port: wsPort,
    shutdownToken,
  }),
  onError: (error) => console.error("Python backend failed:", error),
});

function startBackend() {
  const backend = backendController.start();
  backend.stdout.on("data", (data) => process.stdout.write(`[python] ${data}`));
  backend.stderr.on("data", (data) => process.stderr.write(`[python] ${data}`));
}

function createWindow() {
  const window = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 960,
    minHeight: 720,
    backgroundColor: "#eef1f6",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  window.loadFile(path.join(projectRoot, "src", "dm3c_ecat", "web", "index.html"));
}

app.whenReady().then(async () => {
  Menu.setApplicationMenu(null);
  try {
    startBackend();
    await backendController.waitForReady(`ws://127.0.0.1:${wsPort}`);
  } catch (error) {
    console.error("Python backend did not become ready; closing application:", error);
    app.quit();
    return;
  }
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", (event) => {
  if (quitAfterBackend || !backendController.child || backendController.child.exitCode !== null) return;
  event.preventDefault();
  backendController.shutdown({
    url: `ws://127.0.0.1:${wsPort}`,
    token: shutdownToken,
  }).finally(() => {
    quitAfterBackend = true;
    app.quit();
  });
});
