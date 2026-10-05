// 驱动页：使能 + 模式目录 + PV/PP/HM/CSP 面板 + Jog + 停止。
// 门控布尔全部来自 computeGates（gates.ts 纯函数）。
import { useEffect, useRef, useState } from "react";
import type { Snapshot } from "../api/types";
import type { Gates } from "../logic/gates";
import { MODE_LABELS, PANEL_MODES, VELOCITY_MODES } from "../logic/gates";

const MODES: { mode: string; label: string; sub: string }[] = [
  { mode: "pv", label: "PV", sub: "轮廓速度" },
  { mode: "pp", label: "PP", sub: "轮廓位置" },
  { mode: "vm", label: "VM", sub: "速度模式" },
  { mode: "pt", label: "PT", sub: "轮廓转矩" },
  { mode: "hm", label: "HM", sub: "回零" },
  { mode: "ip", label: "IP", sub: "插补位置" },
  { mode: "csp", label: "CSP", sub: "周期同步位置" },
  { mode: "csv", label: "CSV", sub: "周期同步速度" },
  { mode: "cst", label: "CST", sub: "周期同步转矩" },
];

const MAX_VELOCITY = 100000;
const MAX_POSITION = 2147483647;

interface DrivePageProps {
  snap: Snapshot | null;
  gates: Gates;
  onSwitchMode: (mode: string) => void;
  send: (command: string, body?: Record<string, unknown>) => void;
}

export function DrivePage({ snap, gates, onSwitchMode, send }: DrivePageProps) {
  // 运动数值输入允许空态（null）：清空输入框 ≠ 目标 0，防止误点执行定位
  // 触发一次到绝对零位的运动。
  const [velocity, setVelocity] = useState<number | null>(1000);
  const [accel, setAccel] = useState(0.5);
  const [decel, setDecel] = useState(0.5);
  const [ppTarget, setPpTarget] = useState<number | null>(1000);
  const [ppVelocity, setPpVelocity] = useState<number | null>(1000);
  const [ppAccel, setPpAccel] = useState(0.5);
  const [ppDecel, setPpDecel] = useState(0.5);
  const [ppRelative, setPpRelative] = useState(false);
  const [hmMethod, setHmMethod] = useState(35);
  const [hmOffset, setHmOffset] = useState(0);
  const [hmFast, setHmFast] = useState(500);
  const [hmSlow, setHmSlow] = useState(100);
  const [hmAccelTime, setHmAccelTime] = useState(0.5);
  const [cspTarget, setCspTarget] = useState<number | null>(1000);
  const [cspDuration, setCspDuration] = useState(1);
  const [hintError, setHintError] = useState("");

  const jogTimer = useRef<number | null>(null);
  const jogActive = useRef(false);

  const mode = gates.mode;
  const modeAvailable = gates.modeAvailable;
  const availableModes = snap?.availableModes ?? ["pv", "pp"];
  const enableChecked = snap?.enableRequested ?? false;

  // Jog 心跳：按住期间 120ms 重复发送（与旧实现一致）
  const startJog = (sign: number) => {
    if (!gates.canJog) return;
    if (velocity === null) {
      setHintError("请先输入目标速度。");
      return;
    }
    setHintError("");
    const v = sign * Math.min(MAX_VELOCITY, Math.max(1, Math.abs(velocity) || 1));
    send("jog", { velocity: v });
    if (!jogActive.current) {
      jogActive.current = true;
      jogTimer.current = window.setInterval(() => send("jog", { velocity: v }), 120);
    }
  };
  const stopJog = () => {
    if (!jogActive.current) return;
    jogActive.current = false;
    if (jogTimer.current !== null) {
      window.clearInterval(jogTimer.current);
      jogTimer.current = null;
    }
    send("stop");
  };
  useEffect(() => () => stopJog(), []);

  // 安全网：页面失焦/隐藏时本地必须先掐掉 Jog 重发定时器，再依赖 App 层 stop
  // 兜底；否则 120ms 定时器会在后台继续发送 jog 命令刷新心跳。
  useEffect(() => {
    const halt = () => {
      if (jogActive.current) {
        jogActive.current = false;
        if (jogTimer.current !== null) {
          window.clearInterval(jogTimer.current);
          jogTimer.current = null;
        }
      }
    };
    const onBlur = halt;
    const onVisibility = () => {
      if (document.hidden) halt();
    };
    window.addEventListener("blur", onBlur);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("blur", onBlur);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  const integerInput = (value: number | null): number | null =>
    value === null ? null : Number.isSafeInteger(value) ? value : null;

  const movePp = () => {
    if (!gates.canMovePp) return;
    if (ppTarget === null || ppVelocity === null) {
      setHintError("请输入目标位置和轮廓速度。");
      return;
    }
    const t = integerInput(ppTarget);
    const v = integerInput(ppVelocity);
    if (t === null || v === null) {
      setHintError("位置和速度必须是整数。");
      return;
    }
    setHintError("");
    send("move_pp", {
      targetPosition: t,
      velocity: v,
      accelerationTime: ppAccel,
      decelerationTime: ppDecel,
      relative: ppRelative,
    });
  };

  const startHoming = () => {
    if (!gates.canHome) return;
    if ([hmMethod, hmOffset, hmFast, hmSlow].some((v) => !Number.isSafeInteger(v))) {
      setHintError("回零方法、速度和偏置必须是有效整数。");
      return;
    }
    setHintError("");
    send("start_homing", {
      method: hmMethod,
      fastVelocity: hmFast,
      slowVelocity: hmSlow,
      accelerationTime: hmAccelTime,
      offset: hmOffset,
    });
  };

  const moveCsp = () => {
    if (!gates.canMoveCsp) return;
    if (cspTarget === null) {
      setHintError("请输入目标位置。");
      return;
    }
    const t = integerInput(cspTarget);
    if (t === null || !Number.isFinite(cspDuration)) {
      setHintError("目标位置必须是整数，变化时间必须有效。");
      return;
    }
    setHintError("");
    send("move_csp", { targetPosition: t, duration: cspDuration });
  };

  const jogHint = !enableChecked
    ? "请先打开驱动使能"
    : "按住方向按钮运行，松开停止";
  const ppHint = !enableChecked
    ? "请先打开驱动使能"
    : snap?.ppMoving
      ? "定位执行中，保持页面连接"
      : "参数确认后执行一次定位";
  const hmHint = !enableChecked
    ? "请先打开驱动使能"
    : snap?.homingActive
      ? "回零执行中，保持页面连接"
      : "确认方法、速度和限位后执行回零";
  const cspHint = !enableChecked
    ? "请先打开驱动使能"
    : snap?.cspMoving
      ? "CSP 执行中，保持页面连接"
      : "输入目标位置和变化时间后执行";
  const modeHint =
    snap?.state === "SWITCHING"
      ? `正在切换到 ${mode.toUpperCase()} 模式...`
      : !modeAvailable
        ? "当前驱动的 ESI/固件没有声明此模式，不能切换。"
        : "未使能时可切换运行模式。";

  return (
    <section className="page control-page">
      <div className="grid-2">
        {/* 使能 + 参数 */}
        <div className="card">
          <div className="card-head">
            <h2>驱动使能</h2>
            <label className="switch">
              <input
                type="checkbox"
                checked={enableChecked}
                disabled={!gates.canEnable}
                onChange={(e) => send(e.target.checked ? "enable" : "disable")}
              />
              <span className="switch-track"><span className="switch-thumb" /></span>
            </label>
          </div>
          <div className="card-body">
            <div className="metric-row">
              <span>使能状态</span>
              <strong className="mono">{snap?.enabled ? "已使能" : "未使能"}</strong>
            </div>
            <hr className="sep" />
            <div className="mode-switch mode-catalog" role="group" aria-label="CiA 402 运动模式">
              {MODES.map((item) => {
                const available = availableModes.includes(item.mode as Snapshot["availableModes"][number]);
                return (
                  <button
                    key={item.mode}
                    type="button"
                    className={`mode-option ${item.mode === mode ? "is-active" : ""} ${available ? "" : "is-unavailable"}`}
                    aria-pressed={item.mode === mode}
                    disabled={!gates.canSwitchMode || !available}
                    title={available ? MODE_LABELS[item.mode] : "当前驱动未声明此模式的 PDO"}
                    onClick={() => {
                      if (item.mode !== snap?.motionMode) onSwitchMode(item.mode);
                    }}
                  >
                    <b>{item.label}</b>
                    <small>{item.sub}</small>
                  </button>
                );
              })}
            </div>
            <p className="hint">{hintError || modeHint}</p>
            <p className="hint mode-capability">
              {availableModes.length
                ? `当前驱动支持：${availableModes.map((m) => MODE_LABELS[m] ?? m).join("、")}`
                : "当前驱动未提供可用运动模式。"}
            </p>

            {/* PV 参数 */}
            <div id="pvSettings" className="mode-settings" hidden={!VELOCITY_MODES.has(mode) || !modeAvailable}>
              <label className="field-label">目标速度（驱动单位）</label>
              <input
                className="text-input" type="number" min={1} max={MAX_VELOCITY}
                value={velocity ?? ""} onChange={(e) => setVelocity(e.target.value === "" ? null : Number(e.target.value))}
              />
              <div className="field-2">
                <div>
                  <label className="field-label">加速时间（秒）</label>
                  <input className="text-input" type="number" min={0.01} max={60} step={0.01}
                    value={accel} onChange={(e) => setAccel(Number(e.target.value))} />
                </div>
                <div>
                  <label className="field-label">减速时间（秒）</label>
                  <input className="text-input" type="number" min={0.01} max={60} step={0.01}
                    value={decel} onChange={(e) => setDecel(Number(e.target.value))} />
                </div>
              </div>
              <p className="hint">加速/减速为 0 速↔目标速度的设定时间。</p>
            </div>

            {/* PP 参数 */}
            <div id="ppSettings" className="mode-settings" hidden={mode !== "pp" || !modeAvailable}>
              <label className="field-label">轮廓速度（驱动单位）</label>
              <input className="text-input" type="number" min={1} max={MAX_VELOCITY} step={1}
                value={ppVelocity ?? ""} onChange={(e) => setPpVelocity(e.target.value === "" ? null : Number(e.target.value))} />
              <div className="field-2">
                <div>
                  <label className="field-label">加速时间（秒）</label>
                  <input className="text-input" type="number" min={0.01} max={60} step={0.01}
                    value={ppAccel} onChange={(e) => setPpAccel(Number(e.target.value))} />
                </div>
                <div>
                  <label className="field-label">减速时间（秒）</label>
                  <input className="text-input" type="number" min={0.01} max={60} step={0.01}
                    value={ppDecel} onChange={(e) => setPpDecel(Number(e.target.value))} />
                </div>
              </div>
              <p className="hint">加减速时间按 0 速↔轮廓速度换算为驱动加速度值。</p>
            </div>

            {/* HM 参数 */}
            <div id="hmSettings" className="mode-settings" hidden={mode !== "hm" || !modeAvailable}>
              <div className="field-2">
                <div>
                  <label className="field-label">回零方法（6098）</label>
                  <input className="text-input" type="number" min={-128} max={127} step={1}
                    value={hmMethod} onChange={(e) => setHmMethod(Number(e.target.value))} />
                </div>
                <div>
                  <label className="field-label">回零偏置（驱动脉冲）</label>
                  <input className="text-input" type="number" min={-MAX_POSITION} max={MAX_POSITION} step={1}
                    value={hmOffset} onChange={(e) => setHmOffset(Number(e.target.value))} />
                </div>
              </div>
              <div className="field-2">
                <div>
                  <label className="field-label">快速速度（驱动单位）</label>
                  <input className="text-input" type="number" min={1} max={MAX_VELOCITY} step={1}
                    value={hmFast} onChange={(e) => setHmFast(Number(e.target.value))} />
                </div>
                <div>
                  <label className="field-label">慢速速度（驱动单位）</label>
                  <input className="text-input" type="number" min={1} max={MAX_VELOCITY} step={1}
                    value={hmSlow} onChange={(e) => setHmSlow(Number(e.target.value))} />
                </div>
              </div>
              <label className="field-label">回零加速时间（秒）</label>
              <input className="text-input" type="number" min={0.01} max={60} step={0.01}
                value={hmAccelTime} onChange={(e) => setHmAccelTime(Number(e.target.value))} />
              <p className="hint">回零方法由驱动器固件定义；首次运行前确认限位、原点和安全链路。</p>
            </div>

            {/* CSP 参数 */}
            <div id="cspSettings" className="mode-settings" hidden={mode !== "csp" || !modeAvailable}>
              <label className="field-label">目标变化时间（秒）</label>
              <input className="text-input" type="number" min={0.01} max={60} step={0.01}
                value={cspDuration} onChange={(e) => setCspDuration(Number(e.target.value))} />
              <p className="hint">主站按 10 ms 周期持续发送目标位置，直到到达设定位置。</p>
            </div>

            {/* 无可用 PDO 的模式 */}
            <div
              className="mode-settings mode-unavailable"
              hidden={modeAvailable && PANEL_MODES.has(mode)}
            >
              <strong id="modeUnavailableTitle">
                {MODE_LABELS[mode] ?? mode} 未提供可用 PDO
              </strong>
              <p className="hint">请使用与驱动 ESI 和固件匹配的过程数据映射。</p>
            </div>
          </div>
        </div>

        {/* 运动测试 */}
        <div className="card jog-card">
          <div className="card-head"><h2>运动测试</h2></div>
          <div className="card-body">
            <div id="pvControls" hidden={!VELOCITY_MODES.has(mode) || !modeAvailable}>
              <div className="jog-grid">
                <button
                  type="button"
                  className="jog-btn jog-fwd"
                  disabled={!gates.canJog}
                  onPointerDown={(e) => { e.preventDefault(); startJog(1); }}
                  onPointerUp={stopJog}
                  onPointerCancel={stopJog}
                  onPointerLeave={stopJog}
                  onLostPointerCapture={stopJog}
                >
                  <span className="jog-dir">正转</span>
                  <span className="jog-sub">正向 · 按住运行</span>
                </button>
                <button
                  type="button"
                  className="jog-btn jog-rev"
                  disabled={!gates.canJog}
                  onPointerDown={(e) => { e.preventDefault(); startJog(-1); }}
                  onPointerUp={stopJog}
                  onPointerCancel={stopJog}
                  onPointerLeave={stopJog}
                  onLostPointerCapture={stopJog}
                >
                  <span className="jog-dir">反转</span>
                  <span className="jog-sub">反向 · 按住运行</span>
                </button>
              </div>
              <p className="hint center">{jogHint}</p>
            </div>

            <div id="ppControls" className="pp-controls" hidden={mode !== "pp" || !modeAvailable}>
              <div className="pp-target-field">
                <label className="field-label">目标位置（驱动脉冲）</label>
                <input className="text-input" type="number" min={-MAX_POSITION} max={MAX_POSITION} step={1}
                  value={ppTarget ?? ""} onChange={(e) => setPpTarget(e.target.value === "" ? null : Number(e.target.value))} />
              </div>
              <div className="position-mode" role="group" aria-label="位置类型">
                <label className="position-option">
                  <input type="radio" name="ppPositionMode" value="absolute"
                    checked={!ppRelative} onChange={() => setPpRelative(false)} />
                  <span>绝对位置</span>
                </label>
                <label className="position-option">
                  <input type="radio" name="ppPositionMode" value="relative"
                    checked={ppRelative} onChange={() => setPpRelative(true)} />
                  <span>相对位移（正负方向）</span>
                </label>
              </div>
              <p className="hint">
                {ppRelative
                  ? "相对位移：+ 为正向，- 为反向；每次从当前位置累加位移。"
                  : "绝对位置：目标是坐标值，方向由当前位置和目标坐标比较决定。"}
              </p>
              <div className="pp-action-row">
                <div className="position-readout">
                  <div>
                    <span>当前位置</span>
                    <strong>{snap?.actualPosition ?? 0}</strong>
                  </div>
                </div>
                <button type="button" className="move-btn" disabled={!gates.canMovePp} onClick={movePp}>
                  <span>执行定位</span>
                  <small>发送一个 PP 目标</small>
                </button>
              </div>
              <p className="hint center">{ppHint}</p>
            </div>

            <div id="hmControls" className="mode-controls" hidden={mode !== "hm" || !modeAvailable}>
              <div className="motion-status-row">
                <span>回零状态</span>
                <strong className="mono">
                  {snap?.homingError ? "回零错误"
                    : snap?.homingAttained ? "已回零"
                    : snap?.homingActive ? "执行中"
                    : "未执行"}
                </strong>
              </div>
              <button type="button" className="move-btn" disabled={!gates.canHome} onClick={startHoming}>
                <span>开始回零</span>
                <small>发送一次 Homing 启动</small>
              </button>
              <p className="hint center">{hmHint}</p>
            </div>

            <div id="cspControls" className="mode-controls" hidden={mode !== "csp" || !modeAvailable}>
              <div className="pp-target-field">
                <label className="field-label">目标位置（驱动脉冲）</label>
                <input className="text-input" type="number" min={-MAX_POSITION} max={MAX_POSITION} step={1}
                  value={cspTarget ?? ""} onChange={(e) => setCspTarget(e.target.value === "" ? null : Number(e.target.value))} />
              </div>
              <div className="pp-action-row">
                <div className="position-readout">
                  <div>
                    <span>当前位置</span>
                    <strong>{snap?.actualPosition ?? 0}</strong>
                  </div>
                </div>
                <button type="button" className="move-btn" disabled={!gates.canMoveCsp} onClick={moveCsp}>
                  <span>执行 CSP</span>
                  <small>按周期发送目标</small>
                </button>
              </div>
              <p className="hint center">{cspHint}</p>
            </div>

            <div
              className="mode-controls mode-unavailable"
              hidden={modeAvailable && PANEL_MODES.has(mode)}
            >
              <p className="hint center">当前驱动未声明此模式的过程数据映射，不能发送运动命令。</p>
            </div>

            <button
              type="button"
              className="stop-btn"
              onClick={() => {
                stopJog();
                send("stop");
              }}
            >
              ⏹ 停止运动
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}