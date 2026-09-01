const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { createBackendController, resolveBackendLaunch } = require("../electron/backend_lifecycle.cjs");

class FakeChild extends EventEmitter {
  constructor() {
    super();
    this.exitCode = null;
    this.killCount = 0;
    this.stdout = new EventEmitter();
    this.stderr = new EventEmitter();
  }

  kill() {
    this.killCount += 1;
    this.exitCode = 1;
    this.emit("exit", 1);
  }
}

class FakeSocket extends EventEmitter {
  addEventListener(name, handler) { this.on(name, handler); }
  send(message) { this.sent = message; }
  close() { this.closed = true; }
}

test("backend launch uses py in development and bundled exe when packaged", () => {
  const development = resolveBackendLaunch({
    isPackaged: false,
    resourcesPath: "C:\\app\\resources",
    projectRoot: "D:\\FN\\ECAT",
    port: 8765,
    shutdownToken: "secret",
  });
  assert.equal(development.command, "py");
  assert.deepEqual(development.args, ["-m", "dm3c_ecat.websocket_hmi", "--port", "8765", "--shutdown-token", "secret"]);
  assert.equal(development.cwd, "D:\\FN\\ECAT");
  assert.equal(development.env.PYTHONPATH, "D:\\FN\\ECAT\\src");

  const packaged = resolveBackendLaunch({
    isPackaged: true,
    resourcesPath: "C:\\app\\resources",
    projectRoot: "D:\\FN\\ECAT",
    port: 8765,
    shutdownToken: "secret",
  });
  assert.equal(packaged.command, "C:\\app\\resources\\backend\\SYNTEC-ECAT-Test-Backend.exe");
  assert.deepEqual(packaged.args, ["--port", "8765", "--shutdown-token", "secret"]);
  assert.equal(packaged.cwd, "C:\\app\\resources");
  assert.equal(packaged.env.PYTHONPATH, undefined);
});

test("start is idempotent and shutdown sends an authorized request once", async () => {
  const child = new FakeChild();
  let spawnCount = 0;
  const sockets = [];
  const controller = createBackendController({
    spawnImpl: () => { spawnCount += 1; return child; },
    WebSocketImpl: class extends FakeSocket { constructor() { super(); sockets.push(this); } },
    shutdownTimeoutMs: 50,
  });

  assert.equal(controller.start(), child);
  assert.equal(controller.start(), child);
  assert.equal(spawnCount, 1);
  const shutdown = controller.shutdown({ url: "ws://test", token: "secret" });
  sockets[0].emit("open");
  assert.deepEqual(JSON.parse(sockets[0].sent), { command: "shutdown", token: "secret" });
  sockets[0].emit("message", { data: JSON.stringify({ type: "ack", command: "shutdown" }) });
  child.exitCode = 0;
  child.emit("exit", 0);
  await shutdown;
  assert.equal(child.killCount, 0);
  await controller.shutdown({ url: "ws://test", token: "secret" });
  assert.equal(sockets.length, 1);
});

test("shutdown kills a stuck backend after timeout", async () => {
  const child = new FakeChild();
  let socket;
  const controller = createBackendController({
    spawnImpl: () => child,
    WebSocketImpl: class extends FakeSocket { constructor() { super(); socket = this; } },
    shutdownTimeoutMs: 5,
  });
  controller.start();
  await controller.shutdown({ url: "ws://test", token: "secret" });
  assert.equal(child.killCount, 1);
  assert.equal(socket.closed, true);
});

test("restart resets shutdown state and shuts down the new child", async () => {
  const children = [new FakeChild(), new FakeChild()];
  let spawnCount = 0;
  const sockets = [];
  const controller = createBackendController({
    spawnImpl: () => children[spawnCount++],
    WebSocketImpl: class extends FakeSocket { constructor() { super(); sockets.push(this); } },
    shutdownTimeoutMs: 50,
  });

  const firstChild = controller.start();
  const firstShutdown = controller.shutdown({ url: "ws://test", token: "secret" });
  firstChild.exitCode = 0;
  firstChild.emit("exit", 0);
  await firstShutdown;

  const secondChild = controller.start();
  const secondShutdown = controller.shutdown({ url: "ws://test", token: "secret" });
  sockets[1].emit("open");
  assert.deepEqual(JSON.parse(sockets[1].sent), { command: "shutdown", token: "secret" });
  secondChild.exitCode = 0;
  secondChild.emit("exit", 0);
  await secondShutdown;

  assert.equal(spawnCount, 2);
  assert.equal(firstChild.killCount, 0);
  assert.equal(secondChild.killCount, 0);
  assert.equal(sockets[1].closed, true);
});

test("ready handshake resolves on WebSocket open", async () => {
  const child = new FakeChild();
  let socket;
  const controller = createBackendController({
    spawnImpl: () => child,
    WebSocketImpl: class extends FakeSocket { constructor() { super(); socket = this; } },
    readyTimeoutMs: 50,
  });
  controller.start();
  const ready = controller.waitForReady("ws://test");
  socket.emit("open");
  await ready;
  assert.equal(socket.closed, true);
});

test("ready handshake retries when the backend is still starting", async () => {
  const child = new FakeChild();
  const sockets = [];
  const controller = createBackendController({
    spawnImpl: () => child,
    WebSocketImpl: class extends FakeSocket {
      constructor() {
        super();
        sockets.push(this);
      }
    },
    readyTimeoutMs: 500,
  });
  controller.start();
  const ready = controller.waitForReady("ws://test");
  sockets[0].emit("error");
  await new Promise((resolve) => setTimeout(resolve, 150));
  sockets[1].emit("open");
  await ready;
  assert.equal(sockets.length, 2);
  assert.equal(sockets[0].closed, true);
});