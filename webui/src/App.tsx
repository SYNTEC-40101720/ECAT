// 骨架层：侧边栏导航 + 顶栏状态 + 视图路由 + 安全网（blur/unload/hidden 停止）。
import { useCallback, useEffect, useRef, useState } from "react";
import { useEventStream } from "./api/client";
import type { Snapshot, WeldingMode } from "./api/types";
import { DrivePage } from "./features/DrivePage";
import { IoPage } from "./features/IoPage";
import { WeldingPage } from "./features/WeldingPage";
import {
  computeGates,
  STATE_LABELS,
  stateKind,
} from "./logic/gates";
import "./styles/workbench.css";

type View = "drive" | "io" | "welding";

const THEME_KEY = "ecat-test-theme";

function readTheme(): "light" | "dark" {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    /* ignore */
  }
  return typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function App() {
  const [activeView, setActiveView] = useState<View>("drive");
  const [requestedMode, setRequestedMode] = useState("pv");
  const [requestedWeldingMode, setRequestedWeldingMode] =
    useState<WeldingMode>("dc_unified");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">(readTheme);
  const [adapterError, setAdapterError] = useState("");
  const [selectedAdapter, setSelectedAdapter] = useState<string | null>(null);
  const conn = useEventStream();
  const snap = conn.snapshot;
  const gates = computeGates(snap, conn.hasControl, requestedMode);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  // 模式切换请求落定后跟随服务器确认
  useEffect(() => {
    if (snap && snap.state !== "SWITCHING") {
      setRequestedMode(snap.motionMode || "pv");
    }
  }, [snap]);

  const send = conn.sendCommand;

  const stopAllMotion = useCallback(() => {
    send("stop");
  }, [send]);

  // 安全网：失焦/隐藏/卸载即停止（与旧 app.js 一致）
  useEffect(() => {
    const onBlur = () => stopAllMotion();
    const onVisibility = () => {
      if (document.hidden) stopAllMotion();
    };
    const onBeforeUnload = () => stopAllMotion();
    window.addEventListener("blur", onBlur);
    window.addEventListener("beforeunload", onBeforeUnload);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("blur", onBlur);
      window.removeEventListener("beforeunload", onBeforeUnload);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [stopAllMotion]);

  // 后端拒绝时回显错误；网卡类命令错误显示在设置对话框
  useEffect(() => {
    if (!conn.lastError) return;
    setAdapterError(conn.lastError);
  }, [conn.lastError]);

  // 运动心跳提升到 App 层：keepalive 定时器随页面卸载而消失，任何导航
  // 切页都会停发心跳、触发 350ms 服务端看门狗中止进行中的运动（与旧
  // app.js 的全局心跳一致）。依赖数组只含布尔字段，不含 snap 对象本身，
  // 避免每个 200ms 快照重建 interval 抖动 120ms 节拍。
  const snapRef = useRef(snap);
  snapRef.current = snap;
  const ppMoving = snap?.motionMode === "pp" && snap.ppMoving;
  const homingActive = snap?.motionMode === "hm" && snap.homingActive;
  const cspMoving = snap?.motionMode === "csp" && snap.cspMoving;
  useEffect(() => {
    if (!ppMoving && !homingActive && !cspMoving) return;
    const timer = window.setInterval(() => {
      const s = snapRef.current;
      if (s?.motionMode === "pp" && s.ppMoving) send("pp_keepalive");
      else if (s?.motionMode === "hm" && s.homingActive) send("homing_keepalive");
      else if (s?.motionMode === "csp" && s.cspMoving) send("csp_keepalive");
    }, 120);
    return () => window.clearInterval(timer);
  }, [ppMoving, homingActive, cspMoving, send]);

  // 焊机命令心跳同理提升到 App 层
  const weldingCommandActive = Boolean(snap?.weldingCommandActive);
  useEffect(() => {
    if (!weldingCommandActive) return;
    const timer = window.setInterval(() => send("welding_keepalive"), 120);
    return () => window.clearInterval(timer);
  }, [weldingCommandActive, send]);

  // 视图可用性：默认跳到第一个可用视图
  useEffect(() => {
    const available: Record<View, boolean> = {
      drive: gates.hasDrive,
      io: gates.hasDigitalIo,
      welding: gates.hasWelding,
    };
    if (!available[activeView]) {
      const next: View = gates.hasDrive
        ? "drive"
        : gates.hasDigitalIo
          ? "io"
          : gates.hasWelding
            ? "welding"
            : "drive";
      setActiveView(next);
    }
  }, [gates.hasDrive, gates.hasDigitalIo, gates.hasWelding, activeView]);

  const viewState = (view: View, snap: Snapshot | null) => {
    if (!snap) return "未接入";
    if (snap.state === "ERROR") return "故障";
    if (view === "drive") return snap.driveConnected ?? snap.connected ? "已连接" : "已识别";
    if (view === "io") return snap.ioConnected ?? snap.connected ? "已连接" : "已识别";
    return snap.weldingConnected ?? snap.connected ? "已连接" : "已识别";
  };

  const navItems: { view: View; label: string; ico: string }[] = [
    { view: "drive", label: "驱动", ico: "DRV" },
    { view: "io", label: "I/O", ico: "I/O" },
    { view: "welding", label: "焊机", ico: "WEL" },
  ];

  const pageTitle = gates.hasDrive
    ? "驱动控制"
    : gates.hasDigitalIo
      ? "远程 I/O"
      : gates.hasWelding
        ? "焊机控制"
        : "等待设备";
  const pageSubtitle = activeView === "drive"
    ? "CiA 402 多模式运动测试"
    : activeView === "io"
      ? `${snap?.ioInputChannels ?? 0} 路数字输入监视与 ${snap?.ioOutputChannels ?? 0} 路数字输出控制`
      : "麦格米特原始 PDO 命令与反馈测试";

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">EC</div>
          <div className="brand-text">
            <strong>ECAT TEST</strong>
            <span>控制台</span>
          </div>
        </div>
        <nav className="nav" aria-label="功能界面">
          {navItems.map((item) => {
            const available =
              item.view === "drive"
                ? gates.hasDrive
                : item.view === "io"
                  ? gates.hasDigitalIo
                  : gates.hasWelding;
            const isActive = available && activeView === item.view;
            return (
              <button
                key={item.view}
                type="button"
                className="nav-item"
                aria-current={isActive ? "page" : "false"}
                disabled={!available}
                title={!available ? "当前总线未识别此设备" : isActive ? "当前界面" : `切换到${item.label}界面`}
                onClick={() => setActiveView(item.view)}
              >
                <span className="nav-ico" aria-hidden="true">{item.ico}</span>
                <span className="nav-copy">
                  <b>{item.label}</b>
                  <small>{available ? viewState(item.view, snap) : "未接入"}</small>
                </span>
              </button>
            );
          })}
        </nav>
        <div className="side-note">
          <span className="side-note-label">本地会话</span>
          <span>控制链路仅限本机</span>
        </div>
        <div className="sidebar-foot">
          <span
            className="conn-dot"
            data-state={
              snap?.state === "ERROR" ? "err" : conn.connected ? "on" : "off"
            }
          />
          <span>
            <b className="conn-label">
              {snap?.state === "ERROR" ? "故障" : conn.connected ? "已连接" : "未连接"}
            </b>
            <small> EtherCAT 链路</small>
          </span>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <div>
            <span className="topbar-kicker">ECAT TEST / 操作控制台</span>
            <h1>{activeView === "drive" && !gates.hasDrive ? "等待设备" : pageTitle}</h1>
            <p className="topbar-sub">{pageSubtitle}</p>
          </div>
          <div className="topbar-right">
            <div className="topbar-status">
              <span className="status-pulse" />
              <span>实时链路</span>
            </div>
            <button
              type="button"
              className="theme-toggle"
              aria-label={theme === "dark" ? "切换到浅色主题" : "切换到深色主题"}
              aria-pressed={theme === "dark"}
              onClick={() => {
                const next = theme === "dark" ? "light" : "dark";
                setTheme(next);
                try {
                  localStorage.setItem(THEME_KEY, next);
                } catch {
                  /* ignore */
                }
              }}
            >
              <span aria-hidden="true">{theme === "dark" ? "☀" : "☾"}</span>
            </button>
            <button
              type="button"
              className="settings-button"
              aria-label="打开设置"
              title="打开设置"
              onClick={() => setSettingsOpen(true)}
            >
              <span className="settings-button-icon" aria-hidden="true">⚙</span>
              <span className="settings-button-label">设置</span>
            </button>
            <span className="state-pill" data-state={snap ? stateKind(snap.state) : "warn"}>
              {snap ? STATE_LABELS[snap.state] ?? snap.state : "启动中"}
            </span>
          </div>
        </header>

        <main className="content">
          {conn.lastError && (
            <p className="hint hint-error" role="alert">{conn.lastError}</p>
          )}
          {activeView === "drive" && (
            <DrivePage
              snap={snap}
              gates={gates}
              onSwitchMode={(mode) => {
                setRequestedMode(mode);
                send("set_mode", { mode });
              }}
              send={send}
            />
          )}
          {activeView === "io" && <IoPage snap={snap} send={send} />}
          {activeView === "welding" && (
            <WeldingPage
              snap={snap}
              gates={gates}
              send={send}
              hasControl={conn.hasControl}
              requestedWeldingMode={requestedWeldingMode}
              onRequestWeldingMode={(mode) => setRequestedWeldingMode(mode)}
            />
          )}
        </main>
      </div>

      {settingsOpen && (
        <dialog
          className="settings-dialog"
          open
          onClick={(e) => {
            if (e.target === e.currentTarget) setSettingsOpen(false);
          }}
        >
          <div className="settings-shell">
            <header className="settings-head">
              <div>
                <span className="settings-section-kicker">WORKSPACE SETTINGS</span>
                <h2>设置</h2>
              </div>
              <button
                type="button"
                className="settings-close"
                aria-label="关闭设置"
                title="关闭设置"
                onClick={() => setSettingsOpen(false)}
              >
                ×
              </button>
            </header>
            <div className="settings-body">
              <section className="settings-section">
                <div className="settings-section-head">
                  <div>
                    <span className="settings-section-kicker">连接</span>
                    <h3>EtherCAT 网卡</h3>
                    <p className="adapter-current">
                      当前接口：<code>{snap?.interface ?? "未选择"}</code>
                    </p>
                  </div>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={() => send("list_adapters")}
                  >
                    刷新网卡
                  </button>
                </div>
                <div className="settings-section-body">
                  <div className="adapter-controls">
                    <select
                      className="adapter-select"
                      aria-label="选择 EtherCAT 网卡"
                      disabled={!gates.canSelectAdapter || conn.adapters.length === 0}
                      value={selectedAdapter ?? ""}
                      onChange={(e) => setSelectedAdapter(e.target.value || null)}
                    >
                      <option value="">
                        {conn.adaptersReady
                          ? conn.adapters.length
                            ? "选择物理 EtherCAT 网卡"
                            : "未发现 Npcap 网卡"
                          : "正在检测网卡..."}
                      </option>
                      {conn.adapters.map((a) => (
                        <option key={a.name} value={a.name} disabled={!a.selectable}>
                          {a.description || a.name} · {a.name}
                          {a.selectable ? "" : " · 不可用于 EtherCAT"}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={!gates.canSelectAdapter || !selectedAdapter}
                      onClick={() => {
                        if (selectedAdapter) {
                          send("select_interface", { interface: selectedAdapter });
                          setAdapterError("正在切换网卡...");
                        }
                      }}
                    >
                      应用网卡
                    </button>
                  </div>
                  <p className="hint">
                    {adapterError ||
                      (conn.adaptersReady
                        ? conn.adapters.length
                          ? `发现 ${conn.adapters.length} 个网卡，其中 ${
                              conn.adapters.filter((a) => a.selectable).length
                            } 个可用于 EtherCAT。`
                          : "未发现可选物理网卡，请检查网卡驱动或点击刷新。"
                        : "正在等待网卡列表。")}
                  </p>
                </div>
              </section>
            </div>
          </div>
        </dialog>
      )}
    </div>
  );
}