// 焊机页：五种模式 + 命令开关 + JOB/模拟量 + 起弧互锁 + 反馈面板。
import { useEffect, useState } from "react";
import type { Snapshot, WeldingMode } from "../api/types";
import type { Gates } from "../logic/gates";

const WELDING_MODES: { mode: WeldingMode; label: string; sub: string }[] = [
  { mode: "dc_unified", label: "DC", sub: "直流一元化" },
  { mode: "pulse_unified", label: "PULSE", sub: "脉冲一元化" },
  { mode: "job", label: "JOB", sub: "程序调用" },
  { mode: "remote", label: "近控", sub: "近控模式" },
  { mode: "separate", label: "分别", sub: "分别模式" },
];

interface WeldingPageProps {
  snap: Snapshot | null;
  gates: Gates;
  send: (command: string, body?: Record<string, unknown>) => void;
  hasControl: boolean;
  requestedWeldingMode: WeldingMode;
  onRequestWeldingMode: (mode: WeldingMode) => void;
}

function intIn(value: number, minimum: number, maximum: number): number | null {
  return Number.isInteger(value) && value >= minimum && value <= maximum ? value : null;
}

export function WeldingPage({
  snap,
  gates,
  send,
  hasControl,
  requestedWeldingMode,
  onRequestWeldingMode,
}: WeldingPageProps) {
  const [job, setJob] = useState(0);
  const [currentOrSpeed, setCurrentOrSpeed] = useState(0);
  const [voltageOrStrength, setVoltageOrStrength] = useState(0);
  const [robotReady, setRobotReady] = useState(false);
  const [gasTest, setGasTest] = useState(false);
  const [wireInch, setWireInch] = useState(false);
  const [wireRetract, setWireRetract] = useState(false);
  const [touchEnable, setTouchEnable] = useState(false);
  const [hint, setHint] = useState("");

  const hasWelding = gates.hasWelding;
  const weldingConnected = snap?.weldingConnected ?? (hasWelding && Boolean(snap?.connected));
  const weldingInProgress = Boolean(snap?.weldingStart || snap?.weldingActive);
  const canControl = gates.canControlWelding;
  const mode = snap?.weldingCommandActive ? snap.weldingMode : requestedWeldingMode;

  // 跟随服务器确认的模式
  useEffect(() => {
    if (snap?.weldingCommandActive && snap.weldingMode) {
      onRequestWeldingMode(snap.weldingMode);
    }
  }, [snap?.weldingCommandActive, snap?.weldingMode]);

  // 焊机命令心跳
  useEffect(() => {
    if (!(snap?.weldingCommandActive)) return;
    const timer = window.setInterval(() => send("welding_keepalive"), 120);
    return () => window.clearInterval(timer);
  }, [snap?.weldingCommandActive, send]);

  const sendCommand = (startWelding = false) => {
    const j = intIn(job, 0, 49);
    const c = intIn(currentOrSpeed, 0, 65535);
    const v = intIn(voltageOrStrength, 0, 65535);
    if (j === null || c === null || v === null) {
      setHint("JOB 必须为 0-49 的整数，电流/速度和电压/强度必须为 0-65535 的整数。");
      return false;
    }
    send("set_welding_command", {
      startWelding,
      robotReady,
      mode: requestedWeldingMode,
      gasTest,
      wireInch,
      wireRetract,
      touchEnable,
      job: j,
      currentOrSpeed: c,
      voltageOrStrength: v,
    });
    return true;
  };

  const startWelding = () => {
    if (!gates.canStartWelding) return;
    if (!sendCommand(false)) return;
    send("start_welding");
  };

  const faultCode = snap?.weldingFaultCode ?? 0;
  const communicationReady = Boolean(snap?.weldingCommunicationReady);
  const deviceName = snap?.weldingDevice || (hasWelding ? snap?.device : "麦格米特焊机");

  const modeHint = !hasWelding
    ? "等待识别麦格米特焊机。"
    : !weldingConnected
      ? "焊机尚未进入可通信状态。"
      : snap?.weldingPowerFault
        ? "焊机报告电源故障，开始焊接已禁止。"
        : weldingInProgress
          ? "焊接进行中，保持页面连接。"
          : snap?.weldingCommandActive
            ? "命令已准备，可以继续调整参数或开始焊接。"
            : "选择参数后点击开始焊接。";

  return (
    <section className="page welding-page" aria-labelledby="weldingPageTitle">
      <div className="welding-summary">
        <div className="welding-summary-copy">
          <span className="section-kicker">WELDING MACHINE</span>
          <h2 id="weldingPageTitle">{deviceName}</h2>
          <p aria-live="polite">
            {hasWelding ? snap?.message || "等待焊机过程数据周期。" : "当前设备不是焊机。"}
          </p>
        </div>
        <div className="welding-readout">
          <span>通信</span>
          <strong data-state={communicationReady ? "on" : "off"}>
            {communicationReady ? "就绪" : "未就绪"}
          </strong>
          <small>{deviceName}</small>
        </div>
        <div className="welding-readout welding-readout-status">
          <span>过程数据 WKC</span>
          <strong>{snap?.wkc ?? 0} / {snap?.expectedWkc ?? 0}</strong>
          <small>10 ms 周期</small>
        </div>
        <div className="welding-readout welding-readout-fault">
          <span>故障代码</span>
          <strong>{faultCode}</strong>
          <small>{snap?.weldingArcSuccess ? "起弧成功" : "等待状态"}</small>
        </div>
      </div>
      <div className="grid-2 welding-layout">
        <div className="card welding-card">
          <div className="card-head">
            <h2>焊接命令</h2>
            <span className="badge" data-off={snap?.weldingCommandActive ? "0" : "1"}>
              {snap?.weldingCommandActive ? "活动" : "待命"}
            </span>
          </div>
          <div className="card-body">
            <div className="mode-switch welding-mode-switch" role="group" aria-label="焊接工作模式">
              {WELDING_MODES.map((item) => (
                <button
                  key={item.mode}
                  type="button"
                  className={`mode-option welding-mode-option ${item.mode === mode ? "is-active" : ""}`}
                  aria-pressed={item.mode === mode}
                  disabled={!canControl || weldingInProgress}
                  onClick={() => {
                    onRequestWeldingMode(item.mode);
                    // 立即用新模式发送一次参数命令
                    const j = intIn(job, 0, 49);
                    const c = intIn(currentOrSpeed, 0, 65535);
                    const v = intIn(voltageOrStrength, 0, 65535);
                    if (j === null || c === null || v === null) return;
                    send("set_welding_command", {
                      startWelding: false,
                      robotReady,
                      mode: item.mode,
                      gasTest,
                      wireInch,
                      wireRetract,
                      touchEnable,
                      job: j,
                      currentOrSpeed: c,
                      voltageOrStrength: v,
                    });
                  }}
                >
                  <b>{item.label}</b>
                  <small>{item.sub}</small>
                </button>
              ))}
            </div>
            <p className="hint">{hint || modeHint}</p>
            <div className="welding-toggle-grid">
              {(
                [
                  ["weldingRobotReady", robotReady, setRobotReady, "机器人准备"],
                  ["weldingGasTest", gasTest, setGasTest, "气体检测"],
                  ["weldingWireInch", wireInch, setWireInch, "点动送丝"],
                  ["weldingWireRetract", wireRetract, setWireRetract, "反抽送丝"],
                  ["weldingTouchEnable", touchEnable, setTouchEnable, "寻位使能"],
                ] as const
              ).map(([id, value, setter, label]) => (
                <label key={id} className="welding-toggle">
                  <input
                    type="checkbox"
                    checked={value}
                    disabled={!canControl || weldingInProgress}
                    onChange={(e) => {
                      setter(e.target.checked);
                      // 先更新状态再整体重发参数命令
                      setTimeout(() => sendCommand(false), 0);
                    }}
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>
            <div className="field-2">
              <div>
                <label className="field-label">JOB（0 - 49）</label>
                <input className="text-input" type="number" min={0} max={49} step={1}
                  value={job} disabled={!canControl || weldingInProgress}
                  onChange={(e) => setJob(Number(e.target.value))} />
              </div>
              <div>
                <label className="field-label">焊接电流 / 送丝速度</label>
                <input className="text-input" type="number" min={0} max={65535} step={1}
                  value={currentOrSpeed} disabled={!canControl || weldingInProgress}
                  onChange={(e) => setCurrentOrSpeed(Number(e.target.value))} />
              </div>
            </div>
            <label className="field-label">焊接电压 / 电压强度</label>
            <input className="text-input" type="number" min={0} max={65535} step={1}
              value={voltageOrStrength} disabled={!canControl || weldingInProgress}
              onChange={(e) => setVoltageOrStrength(Number(e.target.value))} />
            <div className="welding-command-actions">
              <button type="button" className="move-btn"
                disabled={!gates.canStartWelding || weldingInProgress}
                onClick={startWelding}>
                <span>开始焊接</span>
                <small>发送起弧命令</small>
              </button>
              <button type="button" className="stop-btn"
                disabled={!hasControl || !hasWelding || !snap?.weldingCommandActive}
                onClick={() => send("stop_welding")}>
                停止焊接
              </button>
            </div>
            <p className="hint io-safety">页面失焦、隐藏或断开连接时会停止焊机命令。</p>
          </div>
        </div>
        <div className="card welding-card">
          <div className="card-head">
            <h2>焊机反馈</h2>
            <span className="badge" data-off={weldingConnected ? "0" : "1"}>
              {weldingConnected ? "实时" : "等待"}
            </span>
          </div>
          <div className="card-body">
            <div className="welding-feedback-grid">
              {(
                [
                  ["起弧成功", snap?.weldingArcSuccess ? "是" : "否"],
                  ["焊接状态", snap?.weldingActive ? "焊接中" : "未焊接"],
                  ["电源故障", snap?.weldingPowerFault ? "故障" : "无"],
                  ["通信就绪", snap?.weldingCommunicationReady ? "是" : "否"],
                  ["寻位成功", snap?.weldingTouchSuccess ? "是" : "否"],
                  ["故障代码", String(faultCode)],
                  ["实际焊接电压", String(snap?.weldingActualVoltage ?? 0)],
                  ["实际焊接电流", String(snap?.weldingActualCurrent ?? 0)],
                  ["实时送丝速度", String(snap?.weldingWireSpeed ?? 0)],
                ] as const
              ).map(([label, value]) => (
                <div key={label} className="welding-metric">
                  <span>{label}</span>
                  <strong>{value}</strong>
                </div>
              ))}
            </div>
            <p className="hint">状态来自焊机 TxPDO 0x1A00，实时量保持原始 uint16 值。</p>
          </div>
        </div>
      </div>
    </section>
  );
}