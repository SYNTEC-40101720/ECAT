const { spawn } = require("node:child_process");
const path = require("node:path");

function resolveBackendLaunch({ isPackaged, resourcesPath, projectRoot, port, shutdownToken }) {
  const args = ["--port", String(port), "--shutdown-token", shutdownToken];
  if (isPackaged) {
    return {
      command: path.join(resourcesPath, "backend", "SYNTEC-ECAT-Test-Backend.exe"),
      args,
      cwd: resourcesPath,
      env: { ...process.env },
    };
  }
  return {
    command: "py",
    args: ["-m", "dm3c_ecat.websocket_hmi", ...args],
    cwd: projectRoot,
    env: { ...process.env, PYTHONPATH: path.join(projectRoot, "src") },
  };
}

function createBackendController({
  spawnImpl = spawn,
  WebSocketImpl = WebSocket,
  command = "py",
  args,
  cwd,
  env,
  shutdownTimeoutMs = 2500,
  readyTimeoutMs = 5000,
  onError = () => {},
} = {}) {
  let child = null;
  let shutdownPromise = null;

  function start() {
    if (child && child.exitCode === null) return child;
    const nextChild = spawnImpl(command, args, { cwd, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    child = nextChild;
    shutdownPromise = null;
    nextChild.on("error", onError);
    return nextChild;
  }

  function waitForReady(url) {
    if (!child || child.exitCode !== null) return Promise.reject(new Error("backend is not running"));
    return new Promise((resolve, reject) => {
      let settled = false;
      let activeSocket = null;
      let retryTimer = null;
      const deadline = Date.now() + readyTimeoutMs;
      const timer = setTimeout(() => finish(new Error("backend ready handshake timed out")), readyTimeoutMs);
      const finish = (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (retryTimer) clearTimeout(retryTimer);
        try { activeSocket?.close(); } catch (_) {}
        if (error) reject(error); else resolve();
      };
      const retry = () => {
        if (settled || retryTimer) return;
        const remaining = deadline - Date.now();
        if (remaining <= 0) return;
        retryTimer = setTimeout(() => {
          retryTimer = null;
          connect();
        }, Math.min(100, remaining));
      };
      const connect = () => {
        if (settled) return;
        let socket;
        try {
          socket = new WebSocketImpl(url);
        } catch (_) {
          retry();
          return;
        }
        activeSocket = socket;
        socket.addEventListener("open", () => finish());
        socket.addEventListener("error", () => {
          if (settled || activeSocket !== socket) return;
          try { socket.close(); } catch (_) {}
          retry();
        });
      };
      connect();
    });
  }

  function shutdown({ url, token } = {}) {
    if (!child || child.exitCode !== null) return Promise.resolve();
    if (shutdownPromise) return shutdownPromise;
    const targetChild = child;
    shutdownPromise = new Promise((resolve) => {
      let settled = false;
      let socket = null;
      const finish = () => {
        if (settled) return;
        settled = true;
        clearTimeout(forceTimer);
        try { socket?.close(); } catch (_) {}
        resolve();
      };
      const forceTimer = setTimeout(() => {
        if (targetChild.exitCode === null) targetChild.kill();
        finish();
      }, shutdownTimeoutMs);
      targetChild.once("exit", finish);
      try {
        socket = new WebSocketImpl(url);
        socket.addEventListener("open", () => {
          socket.send(JSON.stringify({ command: "shutdown", token }));
        });
        socket.addEventListener("message", (event) => {
          let response;
          try { response = JSON.parse(String(event.data)); } catch (_) { return; }
          if (response.type === "ack" && response.command === "shutdown") socket.close();
        });
        socket.addEventListener("error", () => {
          try { socket.close(); } catch (_) {}
        });
      } catch (error) {
        onError(error);
      }
    });
    return shutdownPromise;
  }

  return {
    start,
    waitForReady,
    shutdown,
    get child() { return child; },
  };
}

module.exports = { createBackendController, resolveBackendLaunch };
