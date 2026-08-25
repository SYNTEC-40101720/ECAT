const { app, BrowserWindow, Menu } = require("electron");
const { spawn } = require("node:child_process");
const { randomBytes } = require("node:crypto");
const path = require("node:path");

const projectRoot = path.resolve(__dirname, "..");
const wsPort = 8765;
let backend;
let backendShutdown;
let quitAfterBackend = false;
const shutdownToken = randomBytes(32).toString("hex");

function startBackend() {
  backend = spawn("py", [
    "-m",
    "dm3c_ecat.websocket_hmi",
    "--port",
    String(wsPort),
    "--shutdown-token",
    shutdownToken,
  ], {
    cwd: projectRoot,
    env: { ...process.env, PYTHONPATH: path.join(projectRoot, "src") },
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  backend.stdout.on("data", (data) => process.stdout.write(`[python] ${data}`));
  backend.stderr.on("data", (data) => process.stderr.write(`[python] ${data}`));
  backend.on("error", (error) => console.error("Python backend failed:", error));
}

function shutdownBackend() {
  if (!backend || backend.exitCode !== null) return Promise.resolve();
  if (backendShutdown) return backendShutdown;
  backendShutdown = new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(forceTimer);
      resolve();
    };
    const forceTimer = setTimeout(() => {
      if (backend.exitCode === null) backend.kill();
      finish();
    }, 2500);
    backend.once("exit", finish);
    try {
      const socket = new WebSocket(`ws://127.0.0.1:${wsPort}`);
      socket.addEventListener("open", () => {
        socket.send(JSON.stringify({ command: "shutdown", token: shutdownToken }));
      });
      socket.addEventListener("message", (event) => {
        const response = JSON.parse(String(event.data));
        if (response.type === "ack" && response.command === "shutdown") socket.close();
      });
      socket.addEventListener("error", () => {});
    } catch (error) {
      console.error("Backend graceful shutdown failed:", error);
    }
  });
  return backendShutdown;
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
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", (event) => {
  if (quitAfterBackend || !backend || backend.exitCode !== null) return;
  event.preventDefault();
  shutdownBackend().finally(() => {
    quitAfterBackend = true;
    app.quit();
  });
});
