/* ECAT Test HMI — front-end controller */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  let socket = null;
  let requestedMode = "pv";
  let pendingCommand = "";
  const modeLabels = {
    pp: "PP 轮廓位置",
    vm: "VM 速度模式",
    pv: "PV 轮廓速度",
    pt: "PT 轮廓转矩",
    hm: "HM 回零",
    ip: "IP 插补位置",
    csp: "CSP 周期同步位置",
    csv: "CSV 周期同步速度",
    cst: "CST 周期同步转矩",
  };
  const velocityModes = new Set(["pv", "vm", "csv"]);
  const panelModes = new Set(["pv", "vm", "csv", "pp", "hm", "csp"]);
  const legacyModes = ["pv", "pp"];
  const api = (command, body) => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      pendingCommand = command;
      socket.send(JSON.stringify({ command, ...(body || {}) }));
    }
  };

  const enableSwitch = $("enableSwitch");
  const jogFwd = $("jogFwd");
  const jogRev = $("jogRev");
  const adapterSelect = $("adapterSelect");
  const refreshAdapters = $("refreshAdapters");
  const applyAdapter = $("applyAdapter");
  const themeToggle = $("themeToggle");
  const themeIcon = $("themeIcon");
  const settingsButton = $("settingsButton");
  const settingsDialog = $("settingsDialog");
  const closeSettings = $("closeSettings");
  const modeButtons = [...document.querySelectorAll(".mode-option")];
  const ioInputs = $("ioInputs");
  const ioOutputs = $("ioOutputs");
  const drivePage = document.querySelector(".control-page");
  const ioPage = $("ioPage");
  const viewButtons = [...document.querySelectorAll(".nav-item[data-view]")];
  const themeStorageKey = "ecat-test-theme";
  let activeView = "drive";
  let snap = {
    state: "STARTING",
    enabled: false,
    enableRequested: false,
    connected: false,
    interface: null,
    motionMode: "pv",
    availableModes: legacyModes,
    deviceType: "",
    hasDrive: false,
    hasDigitalIo: false,
    ioInputMask: 0,
    ioOutputMask: 0,
    ioInputMaskHex: "0x0000",
    ioOutputMaskHex: "0x0000",
    ioInputChannels: 0,
    ioOutputChannels: 0,
    ppMoving: false,
    homingActive: false,
    cspMoving: false,
  };
  let adapters = [];

  function readThemePreference() {
    try {
      const stored = localStorage.getItem(themeStorageKey);
      if (stored === "light" || stored === "dark") return stored;
    } catch (err) {}
    return typeof window.matchMedia === "function"
      && window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }

  function setTheme(theme, persist = false) {
    const selectedTheme = theme === "dark" ? "dark" : "light";
    const dark = selectedTheme === "dark";
    document.documentElement.dataset.theme = selectedTheme;
    themeIcon.textContent = dark ? "☀" : "☾";
    const nextLabel = dark ? "切换到浅色主题" : "切换到深色主题";
    themeToggle.setAttribute("aria-label", nextLabel);
    themeToggle.setAttribute("title", nextLabel);
    themeToggle.setAttribute("aria-pressed", String(dark));
    if (persist) {
      try { localStorage.setItem(themeStorageKey, selectedTheme); } catch (err) {}
    }
  }

  themeToggle.addEventListener("click", () => {
    const currentTheme = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
    setTheme(currentTheme === "dark" ? "light" : "dark", true);
  });
  setTheme(readThemePreference());

  settingsButton.addEventListener("click", () => settingsDialog.showModal());
  closeSettings.addEventListener("click", () => settingsDialog.close());
  settingsDialog.addEventListener("click", (event) => {
    if (event.target === settingsDialog) settingsDialog.close();
  });

  function viewMode() {
    return snap.state === "SWITCHING" ? requestedMode : snap.motionMode;
  }

  function channelCount(value) {
    const count = Number(value);
    return Number.isInteger(count) && count > 0 ? count : 0;
  }

  function channelRange(prefix, count) {
    return count > 0
      ? `${prefix} 00 - ${prefix} ${String(count - 1).padStart(2, "0")}`
      : "未识别通道";
  }

  function maskValue(value) {
    if (typeof value === "bigint") return value;
    if (typeof value === "string") {
      try { return BigInt(value); } catch (err) { return 0n; }
    }
    const number = Number(value);
    return Number.isFinite(number) ? BigInt(Math.trunc(number)) : 0n;
  }

  function formatMask(value, channels) {
    const width = Math.max(4, Math.ceil(channels / 4));
    const mask = maskValue(value);
    return `0x${mask.toString(16).padStart(width, "0").toUpperCase()}`;
  }

  function appendChannelText(parent, className, text) {
    const element = document.createElement("span");
    element.className = className;
    element.textContent = text;
    parent.append(element);
    return element;
  }

  function buildIoChannels(inputChannelTotal, outputChannelTotal) {
    const inputChannels = channelCount(inputChannelTotal);
    const outputChannels = channelCount(outputChannelTotal);
    if (
      ioInputs.childElementCount === inputChannels
      && ioOutputs.childElementCount === outputChannels
    ) return;
    ioInputs.replaceChildren();
    ioOutputs.replaceChildren();
    for (let channel = 0; channel < inputChannels; channel += 1) {
      const inputItem = document.createElement("div");
      inputItem.className = "io-channel";
      inputItem.dataset.channel = String(channel);
      appendChannelText(inputItem, "io-channel-label", `DI ${String(channel).padStart(2, "0")}`);
      appendChannelText(inputItem, "io-channel-name", `输入 ${channel}`);
      const inputSignal = document.createElement("span");
      inputSignal.className = "io-signal";
      inputSignal.dataset.active = "0";
      inputSignal.setAttribute("aria-hidden", "true");
      inputItem.append(inputSignal);
      const inputState = appendChannelText(inputItem, "io-signal-state", "OFF");
      inputState.dataset.state = "off";
      ioInputs.append(inputItem);
    }

    for (let channel = 0; channel < outputChannels; channel += 1) {
      const outputItem = document.createElement("label");
      outputItem.className = "io-channel io-output-channel";
      outputItem.dataset.channel = String(channel);
      appendChannelText(outputItem, "io-channel-label", `DO ${String(channel).padStart(2, "0")}`);
      appendChannelText(outputItem, "io-channel-name", `输出 ${channel}`);
      const outputControl = document.createElement("span");
      outputControl.className = "io-output-control";
      const outputInput = document.createElement("input");
      outputInput.type = "checkbox";
      outputInput.className = "io-output-input";
      outputInput.dataset.channel = String(channel);
      outputInput.addEventListener("change", () => {
        if (!outputInput.disabled) {
          api("set_output", { channel, enabled: outputInput.checked });
        }
      });
      const outputTrack = document.createElement("span");
      outputTrack.className = "io-output-track";
      outputTrack.setAttribute("aria-hidden", "true");
      outputControl.append(outputInput, outputTrack);
      outputItem.append(outputControl);
      ioOutputs.append(outputItem);
    }
  }

  function renderIo(s) {
    const isDigitalIo = s.hasDigitalIo ?? (
      s.deviceType === "digital_io" || s.deviceType === "mixed"
    );
    const inputMask = maskValue(s.ioInputMaskHex ?? s.ioInputMask);
    const outputMask = maskValue(s.ioOutputMaskHex ?? s.ioOutputMask);
    const inputChannels = channelCount(s.ioInputChannels);
    const outputChannels = channelCount(s.ioOutputChannels);
    const ioConnected = s.ioConnected ?? (isDigitalIo && s.connected);
    const outputEnabled = isDigitalIo && ioConnected && s.state !== "ERROR";
    const deviceName = s.ioDevice || s.device || "数字 I/O";
    buildIoChannels(inputChannels, outputChannels);
    $("ioPageTitle").textContent = deviceName;
    $("ioInputMask").textContent = formatMask(inputMask, inputChannels);
    $("ioOutputMask").textContent = formatMask(outputMask, outputChannels);
    $("ioInputRange").textContent = channelRange("DI", inputChannels);
    $("ioOutputRange").textContent = channelRange("DO", outputChannels);
    ioInputs.setAttribute("aria-label", `${inputChannels} 路数字输入`);
    ioOutputs.setAttribute("aria-label", `${outputChannels} 路数字输出`);
    $("ioWkc").textContent = `${s.wkc ?? 0} / ${s.expectedWkc ?? 0}`;
    $("ioDeviceName").textContent = deviceName;
    $("ioMessage").textContent = isDigitalIo
      ? (s.message || "等待数字 I/O 周期。")
      : "当前设备不是数字 I/O。";
    $("ioInputBadge").textContent = isDigitalIo && ioConnected ? "实时" : "等待";
    $("ioInputBadge").dataset.off = isDigitalIo && ioConnected ? "0" : "1";
    $("ioOutputBadge").textContent = outputEnabled ? "可控" : "禁止";
    $("ioOutputBadge").dataset.off = outputEnabled ? "0" : "1";
    [...ioInputs.children].forEach((item, channel) => {
      const active = ((inputMask >> BigInt(channel)) & 1n) === 1n;
      const signal = item.querySelector(".io-signal");
      const state = item.querySelector(".io-signal-state");
      signal.dataset.active = active ? "1" : "0";
      state.textContent = active ? "ON" : "OFF";
      state.dataset.state = active ? "on" : "off";
      item.setAttribute("aria-label", `DI ${String(channel).padStart(2, "0")} ${active ? "ON" : "OFF"}`);
    });
    [...ioOutputs.children].forEach((item, channel) => {
      const outputInput = item.querySelector(".io-output-input");
      outputInput.checked = ((outputMask >> BigInt(channel)) & 1n) === 1n;
      outputInput.disabled = !outputEnabled;
      item.setAttribute("aria-label", `DO ${String(channel).padStart(2, "0")}`);
    });
  }

  function updateNavigation(hasDrive, hasDigitalIo) {
    const available = { drive: hasDrive, io: hasDigitalIo };
    if (!available[activeView]) {
      activeView = hasDrive ? "drive" : hasDigitalIo ? "io" : "drive";
    }
    viewButtons.forEach((button) => {
      const view = button.dataset.view;
      const isAvailable = Boolean(available[view]);
      const isActive = isAvailable && view === activeView;
      const connected = view === "drive"
        ? (snap.driveConnected ?? snap.connected)
        : (snap.ioConnected ?? snap.connected);
      const status = button.querySelector(".nav-copy small");
      button.disabled = !isAvailable;
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-current", isActive ? "page" : "false");
      button.title = !isAvailable
        ? "当前总线未识别此设备"
        : isActive
          ? "当前界面"
          : `切换到${view === "drive" ? "驱动" : "I/O"}界面`;
      if (status) {
        status.textContent = !isAvailable
          ? "未接入"
          : snap.state === "ERROR"
            ? "故障"
            : connected
              ? "已连接"
              : "已识别";
      }
    });
  }

  function updateControls() {
    const hasDrive = snap.hasDrive ?? (
      snap.deviceType === "drive" || snap.deviceType === "mixed"
    );
    const hasDigitalIo = snap.hasDigitalIo ?? (
      snap.deviceType === "digital_io" || snap.deviceType === "mixed"
    );
    updateNavigation(hasDrive, hasDigitalIo);
    const showDrive = hasDrive && activeView === "drive";
    const showDigitalIo = hasDigitalIo && activeView === "io";
    drivePage.hidden = !showDrive;
    ioPage.hidden = !showDigitalIo;
    $("pageTitle").textContent = showDrive
      ? "驱动控制"
      : showDigitalIo
        ? "远程 I/O"
        : "等待设备";
    $("pageSubtitle").textContent = showDrive
      ? "CiA 402 多模式运动测试"
      : showDigitalIo
        ? `${channelCount(snap.ioInputChannels)} 路数字输入监视与 ${channelCount(snap.ioOutputChannels)} 路数字输出控制`
        : "连接 EtherCAT 设备后可选择操作界面";
    const mode = viewMode();
    const availableModes = Array.isArray(snap.availableModes)
      ? snap.availableModes
      : legacyModes;
    const modeAvailable = availableModes.includes(mode);
    const hasMotion = snap.moving ?? Boolean(
      snap.velocityCommand || snap.ppMoving || snap.homingActive || snap.cspMoving
    );
    const canEnable = hasDrive
      && snap.connected
      && snap.state !== "ERROR"
      && snap.state !== "SWITCHING";
    const canSelectAdapter = !snap.enableRequested
      && !snap.enabled
      && !snap.velocityCommand
      && !hasMotion
      && snap.state !== "SWITCHING";
    const canSwitchMode = hasDrive
      && snap.connected
      && !snap.enableRequested
      && !snap.enabled
      && snap.state !== "ERROR"
      && snap.state !== "SWITCHING";
    const canJog = velocityModes.has(mode) && snap.enableRequested && snap.state !== "ERROR";
    const canMovePp = mode === "pp"
      && snap.enabled
      && !snap.ppMoving
      && snap.state !== "ERROR";
    const canHome = mode === "hm"
      && snap.enabled
      && !snap.homingActive
      && snap.state !== "ERROR";
    const canMoveCsp = mode === "csp"
      && snap.enabled
      && !snap.cspMoving
      && snap.state !== "ERROR";

    enableSwitch.disabled = !canEnable;
    adapterSelect.disabled = !canSelectAdapter || adapters.length === 0;
    applyAdapter.disabled = adapterSelect.disabled || !adapterSelect.value;
    modeButtons.forEach((button) => {
      const available = availableModes.includes(button.dataset.mode);
      button.disabled = !canSwitchMode || !available;
      button.classList.toggle("is-unavailable", !available);
      button.title = available
        ? modeLabels[button.dataset.mode] || button.dataset.mode
        : "当前驱动未声明此模式的 PDO";
      const active = button.dataset.mode === mode;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    $("pvSettings").hidden = !velocityModes.has(mode) || !modeAvailable;
    $("ppSettings").hidden = mode !== "pp" || !modeAvailable;
    $("hmSettings").hidden = mode !== "hm" || !modeAvailable;
    $("cspSettings").hidden = mode !== "csp" || !modeAvailable;
    $("modeUnavailableSettings").hidden = modeAvailable && panelModes.has(mode);
    $("pvControls").hidden = !velocityModes.has(mode) || !modeAvailable;
    $("ppControls").hidden = mode !== "pp" || !modeAvailable;
    $("hmControls").hidden = mode !== "hm" || !modeAvailable;
    $("cspControls").hidden = mode !== "csp" || !modeAvailable;
    $("modeUnavailableControls").hidden = modeAvailable && panelModes.has(mode);
    jogFwd.disabled = !canJog;
    jogRev.disabled = !canJog;
    $("movePp").disabled = !canMovePp;
    $("startHoming").disabled = !canHome;
    $("moveCsp").disabled = !canMoveCsp;
    $("jogHint").textContent = !snap.enableRequested
      ? "请先打开驱动使能"
      : "按住方向按钮运行，松开停止";
    $("ppHint").textContent = !snap.enableRequested
      ? "请先打开驱动使能"
      : snap.ppMoving
        ? "定位执行中，保持页面连接"
        : "参数确认后执行一次定位";
    $("hmHint").textContent = !snap.enableRequested
      ? "请先打开驱动使能"
      : snap.homingActive
        ? "回零执行中，保持页面连接"
        : "确认方法、速度和限位后执行回零";
    $("cspHint").textContent = !snap.enableRequested
      ? "请先打开驱动使能"
      : snap.cspMoving
        ? "CSP 执行中，保持页面连接"
        : "输入目标位置和变化时间后执行";
    $("modeCapabilityHint").textContent = availableModes.length
      ? `当前驱动支持：${availableModes.map((item) => modeLabels[item] || item).join("、")}`
      : "当前驱动未提供可用运动模式。";
    $("modeUnavailableTitle").textContent = `${modeLabels[mode] || mode} 未提供可用 PDO`;
    $("modeHint").textContent = snap.state === "SWITCHING"
      ? `正在切换到 ${mode.toUpperCase()} 模式...`
      : !modeAvailable
        ? "当前驱动的 ESI/固件没有声明此模式，不能切换。"
        : "未使能时可切换运行模式。";
    renderIo(snap);
  }

  viewButtons.forEach((button) => {
    button.addEventListener("click", () => {
      if (button.disabled || !button.dataset.view) return;
      activeView = button.dataset.view;
      updateControls();
    });
  });

  function renderAdapters(items) {
    adapters = Array.isArray(items)
      ? items.filter((item) => item && item.name)
      : [];
    const selectableAdapters = adapters.filter((item) => item.selectable);
    const selected = adapterSelect.value || snap.interface;
    adapterSelect.replaceChildren();
    if (adapters.length === 0) {
      adapterSelect.add(new Option("未发现 Npcap 网卡", ""));
      $("adapterHint").textContent = "未发现可选物理网卡，请检查网卡驱动或点击刷新。";
    } else {
      adapterSelect.add(new Option(
        selectableAdapters.length
          ? "选择物理 EtherCAT 网卡"
          : "已发现网卡，但没有可用的物理 Ethernet 网卡",
        "",
      ));
      adapters.forEach((adapter) => {
        const option = new Option(
          `${adapter.description || adapter.desc || "未命名网卡"} · ${adapter.name}`,
          adapter.name,
        );
        option.disabled = !adapter.selectable;
        if (!adapter.selectable) {
          option.textContent += " · 不可用于 EtherCAT";
        }
        adapterSelect.add(option);
      });
      const selectedAdapter = selectableAdapters.find((adapter) => adapter.name === selected);
      adapterSelect.value = selectedAdapter
        ? selected
        : "";
      $("adapterHint").textContent = selectableAdapters.length
        ? `发现 ${adapters.length} 个网卡，其中 ${selectableAdapters.length} 个可用于 EtherCAT。`
        : `发现 ${adapters.length} 个网卡，但没有被识别为可用的物理 Ethernet 网卡。`;
    }
    updateControls();
  }

  refreshAdapters.addEventListener("click", () => api("list_adapters"));
  applyAdapter.addEventListener("click", () => {
    if (applyAdapter.disabled || !adapterSelect.value) return;
    $("adapterHint").textContent = "正在切换网卡...";
    api("select_interface", { interface: adapterSelect.value });
  });

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
  let motionHeartbeatTimer = null;
  function stopMotionHeartbeat() {
    if (motionHeartbeatTimer !== null) clearInterval(motionHeartbeatTimer);
    motionHeartbeatTimer = null;
  }
  function startMotionHeartbeat() {
    if (motionHeartbeatTimer !== null) return;
    motionHeartbeatTimer = setInterval(() => {
      if (snap.motionMode === "pp" && snap.ppMoving) {
        api("pp_keepalive");
      } else if (snap.motionMode === "hm" && snap.homingActive) {
        api("homing_keepalive");
      } else if (snap.motionMode === "csp" && snap.cspMoving) {
        api("csp_keepalive");
      } else {
        stopMotionHeartbeat();
      }
    }, 120);
  }
  function startHoming() {
    if ($("startHoming").disabled) return;
    const method = integerInput("homingMethod");
    const fastVelocity = integerInput("homingFast");
    const slowVelocity = integerInput("homingSlow");
    const accelerationTime = durationInput("homingAccelTime");
    const offset = integerInput("homingOffset");
    if ([method, fastVelocity, slowVelocity, offset].some((value) => value === null)
      || accelerationTime === null) {
      $("hmHint").textContent = "回零方法、速度和偏置必须是有效整数，加速时间必须有效。";
      return;
    }
    api("start_homing", {
      method,
      fastVelocity,
      slowVelocity,
      accelerationTime,
      offset,
    });
    startMotionHeartbeat();
  }
  $("startHoming").addEventListener("click", startHoming);

  function moveCsp() {
    if ($("moveCsp").disabled) return;
    const targetPosition = integerInput("cspTarget");
    const duration = durationInput("cspDuration");
    if (targetPosition === null || duration === null) {
      $("cspHint").textContent = "目标位置必须是整数，变化时间必须有效。";
      return;
    }
    api("move_csp", { targetPosition, duration });
    startMotionHeartbeat();
  }
  $("moveCsp").addEventListener("click", moveCsp);

  function integerInput(id) {
    const value = Number($(id).value);
    return Number.isSafeInteger(value) ? value : null;
  }
  function durationInput(id) {
    const value = Number($(id).value);
    return Number.isFinite(value) ? value : null;
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
    const accelerationTime = durationInput("ppAccel");
    const decelerationTime = durationInput("ppDecel");
    if ([targetPosition, velocity].some((value) => value === null)
      || [accelerationTime, decelerationTime].some((value) => value === null)) {
      $("ppHint").textContent = "位置和速度必须是整数，加减速时间必须有效。";
      return;
    }
    const relative = document.querySelector("input[name='ppPositionMode']:checked").value === "relative";
    api("move_pp", {
      targetPosition,
      velocity,
      accelerationTime,
      decelerationTime,
      relative,
    });
    startMotionHeartbeat();
  }
  $("movePp").addEventListener("click", movePp);

  function stopAllMotion() {
    stopJog();
    stopMotionHeartbeat();
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
    HOMING: "回零中",
    CSP_MOVING: "CSP 执行中",
    WAITING_INTERFACE: "等待选择网卡",
    ERROR: "故障",
  };
  function stateKind(s) {
    if (s === "ERROR") return "err";
    if (s === "OPERATIONAL" || s === "ENABLED" || s === "JOGGING" || s === "PP_MOVING" || s === "HOMING" || s === "CSP_MOVING") return "ok";
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
    $("currentInterface").textContent = s.interface || "未选择";
    if (!adapterSelect.value && s.interface) {
      const currentOption = [...adapterSelect.options].find(
        (option) => option.value === s.interface && !option.disabled
      );
      if (currentOption) adapterSelect.value = s.interface;
    }
    $("actualPosition").textContent = String(s.actualPosition ?? 0);
    $("cspActualPosition").textContent = String(s.actualPosition ?? 0);
    $("homingState").textContent = s.homingError
      ? "回零错误"
      : s.homingAttained
        ? "已回零"
        : s.homingActive
          ? "执行中"
          : "未执行";
    if (s.enableRequested !== enableSwitch.checked && !enableSwitch.disabled) {
      enableSwitch.checked = s.enableRequested;
    }
    if (s.ppMoving || s.homingActive || s.cspMoving) startMotionHeartbeat();
    else stopMotionHeartbeat();
    updateControls();
  }

  /* ---------- WebSocket stream ---------- */
  function connectStream() {
    socket = new WebSocket("ws://127.0.0.1:8765");
    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === "state") render(message.data);
        if (message.type === "adapters") renderAdapters(message.data);
        if (message.type === "ack") pendingCommand = "";
        if (message.type === "error") {
          requestedMode = snap.motionMode || "pv";
          const target = pendingCommand === "select_interface" || pendingCommand === "list_adapters"
            ? "adapterHint"
            : pendingCommand === "set_output"
              ? "ioOutputHint"
              : "modeHint";
          $(target).textContent = message.error;
          pendingCommand = "";
          updateControls();
        }
      } catch (err) {}
    };
    socket.onerror = () => {
    };
    socket.onclose = () => {
      stopMotionHeartbeat();
      setTimeout(connectStream, 1000);
    };
  }

  /* ---------- Safety nets ---------- */
  window.addEventListener("blur", stopAllMotion);
  window.addEventListener("beforeunload", stopAllMotion);
  document.addEventListener("visibilitychange", () => { if (document.hidden) stopAllMotion(); });

  updateControls();
  renderAdapters([]);
  connectStream();
})();
