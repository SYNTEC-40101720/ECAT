const { app, BrowserWindow, Menu } = require("electron");
const { spawn } = require("node:child_process");
const path = require("node:path");

const projectRoot = path.resolve(__dirname, "..");
const wsPort = 8765;
let backend;

function startBackend() {
  backend = spawn("py", ["-m", "dm3c_ecat.websocket_hmi", "--port", String(wsPort)], {
    cwd: projectRoot,
    env: { ...process.env, PYTHONPATH: path.join(projectRoot, "src") },
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  backend.stdout.on("data", (data) => process.stdout.write(`[python] ${data}`));
  backend.stderr.on("data", (data) => process.stderr.write(`[python] ${data}`));
  backend.on("error", (error) => console.error("Python backend failed:", error));
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

app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  startBackend();
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (backend) backend.kill();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  if (backend) backend.kill();
});
