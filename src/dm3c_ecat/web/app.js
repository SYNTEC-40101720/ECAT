/* ECAT Test HMI — front-end controller */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  let socket = null;
  let requestedMode = "pv";
  const api = (command, body) => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ command, ...(body || {}) }));
    }
  };

  /* ---------- Controls state ---------- */
  const enableSwitch = $("enableSwitch");
  const jogFwd = $("jogFwd");
  const jogRev = $("jogRev");
  const modeButtons = [...document.querySelectorAll(".mode-option")];
  let snap = {
    state: "STARTING",
    enabled: false,
    enableRequested: false,
    connected: false,
    motionMode: "pv",
    ppMoving: false,
  };

  function viewMode() {
    return snap.state === "SWITCHING" ? requestedMode : snap.motionMode;
  }

  function updateControls() {
    const mode = viewMode();
    const canEnable = snap.connected && snap.state !== "ERROR" && snap.state !== "SWITCHING";
    const canSwitchMode = snap.connected
      && !snap.enableRequested
      && !snap.enabled
      && snap.state !== "ERROR"
      && snap.state !== "SWITCHING";
    const canJog = mode === "pv" && snap.enableRequested && snap.state !== "ERROR";
    const canMovePp = mode === "pp"
      && snap.enabled
      && !snap.ppMoving
      && snap.state !== "ERROR";

    enableSwitch.disabled = !canEnable;
    modeButtons.forEach((button) => {
      button.disabled = !canSwitchMode;
      const active = button.dataset.mode === mode;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    $("pvSettings").hidden = mode !== "pv";
    $("ppSettings").hidden = mode !== "pp";
    $("pvControls").hidden = mode !== "pv";
    $("ppControls").hidden = mode !== "pp";
    jogFwd.disabled = !canJog;
    jogRev.disabled = !canJog;
    $("movePp").disabled = !canMovePp;
    $("jogHint").textContent = !snap.enableRequested
      ? "请先打开驱动使能"
      : "按住方向按钮运行，松开停止";
    $("ppHint").textContent = !snap.enableRequested
      ? "请先打开驱动使能"
      : snap.ppMoving
        ? "定位执行中，保持页面连接"
        : "参数确认后执行一次定位";
    $("modeHint").textContent = snap.state === "SWITCHING"
      ? `正在切换到 ${mode.toUpperCase()} 模式...`
      : "未使能时可切换运行模式。";
  }

  /* ---------- Enable and mode ---------- */
  enableSwitch.addEventListener("change", () => {
    if (enableSwitch.checked) api("enable");
    else api("disable");
  });

  modeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      if (button.disabled) return;
      const mode = button.dataset.mode;
      if (!mode || mode === snap.motionMode) return;
      requestedMode = mode;
      updateControls();
      api("set_mode", { mode });
    });
  });

  /* ---------- Jog press-and-hold ---------- */
  let jogTimer = null;
  let jogActive = false;
  function startJog(sign) {
    if (jogFwd.disabled && jogRev.disabled) return;
    const v = Math.min(
      100000,
      Math.max(1, Math.abs(parseInt($("velocity").value, 10) || 0))
    );
    const velocity = sign * v;
    api("jog", { velocity });
    if (!jogActive) {
      jogActive = true;
      jogTimer = setInterval(() => api("jog", { velocity }), 120);
    }
  }
  function stopJog() {
    if (!jogActive) return;
    jogActive = false;
    clearInterval(jogTimer);
    jogTimer = null;
    api("stop");
  }
  [["jogFwd", 1], ["jogRev", -1]].forEach(([id, sign]) => {
    const el = $(id);
    el.addEventListener("pointerdown", (e) => { e.preventDefault(); startJog(sign); });
    ["pointerup", "pointercancel", "pointerleave", "lostpointercapture"].forEach((ev) =>
      el.addEventListener(ev, stopJog)
    );
  });

  /* ---------- Profile position ---------- */
  let ppHeartbeatTimer = null;
  function stopPpHeartbeat() {
    if (ppHeartbeatTimer !== null) clearInterval(ppHeartbeatTimer);
    ppHeartbeatTimer = null;
  }
  function startPpHeartbeat() {
    if (ppHeartbeatTimer !== null) return;
    ppHeartbeatTimer = setInterval(() => {
      if (snap.motionMode !== "pp" || !snap.ppMoving) {
        stopPpHeartbeat();
        return;
      }
      api("pp_keepalive");
    }, 120);
  }
  function integerInput(id) {
    const value = Number($(id).value);
    return Number.isSafeInteger(value) ? value : null;
  }
  function updatePpModeHint() {
    const relative = document.querySelector("input[name='ppPositionMode']:checked").value === "relative";
    $("ppModeHint").textContent = relative
      ? "相对位移：+ 为正向，- 为反向；每次从当前位置累加位移。"
      : "绝对位置：目标是坐标值，方向由当前位置和目标坐标比较决定。";
  }
  document.querySelectorAll("input[name='ppPositionMode']").forEach((input) => {
    input.addEventListener("change", updatePpModeHint);
  });
  function movePp() {
    if ($("movePp").disabled) return;
    const targetPosition = integerInput("ppTarget");
    const velocity = integerInput("ppVelocity");
    const acceleration = integerInput("ppAccel");
    const deceleration = integerInput("ppDecel");
    if ([targetPosition, velocity, acceleration, deceleration].some((value) => value === null)) {
      $("ppHint").textContent = "位置、速度和加减速必须是整数。";
      return;
    }
    const relative = document.querySelector("input[name='ppPositionMode']:checked").value === "relative";
    api("move_pp", { targetPosition, velocity, acceleration, deceleration, relative });
    startPpHeartbeat();
  }
  $("movePp").addEventListener("click", movePp);

  function stopAllMotion() {
    stopJog();
    stopPpHeartbeat();
    api("stop");
  }
  $("stopBtn").addEventListener("click", stopAllMotion);

  /* ---------- Ramp inputs ---------- */
  function pushRamp() {
    api("set_ramp", {
      acceleration: parseFloat($("accel").value),
      deceleration: parseFloat($("decel").value),
    });
  }
  ["accel", "decel"].forEach((id) => $(id).addEventListener("change", pushRamp));
  updatePpModeHint();

  /* ---------- Snapshot rendering ---------- */
  const stateLabels = {
    STARTING: "启动中",
    SWITCHING: "切换模式",
    ENABLING: "使能中",
    OPERATIONAL: "运行中",
    ENABLED: "已使能",
    JOGGING: "点动中",
    PP_MOVING: "PP 执行中",
    ERROR: "故障",
  };
  function stateKind(s) {
    if (s === "ERROR") return "err";
    if (s === "OPERATIONAL" || s === "ENABLED" || s === "JOGGING" || s === "PP_MOVING") return "ok";
    return "warn";
  }
  function render(s) {
    snap = s;
    if (s.state !== "SWITCHING") requestedMode = s.motionMode || "pv";
    const pill = $("statePill");
    pill.textContent = stateLabels[s.state] || s.state;
    pill.dataset.state = stateKind(s.state);

    const dot = $("connDot");
    if (s.state === "ERROR") { dot.dataset.state = "err"; $("connLabel").textContent = "故障"; }
    else if (s.connected) { dot.dataset.state = "on"; $("connLabel").textContent = "已连接"; }
    else { dot.dataset.state = "off"; $("connLabel").textContent = "未连接"; }

    $("enableState").textContent = s.enabled ? "已使能" : "未使能";
    $("actualPosition").textContent = String(s.actualPosition ?? 0);
    $("targetPosition").textContent = String(s.targetPosition ?? 0);
    if (s.enabled !== enableSwitch.checked && !enableSwitch.disabled) {
      enableSwitch.checked = s.enabled;
    }
    if (s.ppMoving) startPpHeartbeat();
    else stopPpHeartbeat();
    updateControls();
  }

  /* ---------- WebSocket stream ---------- */
  function connectStream() {
    socket = new WebSocket("ws://127.0.0.1:8765");
    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === "state") render(message.data);
        if (message.type === "error") {
          requestedMode = snap.motionMode || "pv";
          $("modeHint").textContent = message.error;
          updateControls();
        }
      } catch (err) {}
    };
    socket.onerror = () => {
    };
    socket.onclose = () => {
      stopPpHeartbeat();
      setTimeout(connectStream, 1000);
    };
  }

  /* ---------- Safety nets ---------- */
  window.addEventListener("blur", stopAllMotion);
  window.addEventListener("beforeunload", stopAllMotion);
  document.addEventListener("visibilitychange", () => { if (document.hidden) stopAllMotion(); });

  updateControls();
  connectStream();
})();
