// 安全门控纯函数：从旧 app.js updateControls() 移植的 can* 布尔组合。
// 全部纯逻辑，可被 vitest 直接覆盖（后续批次补测试文件）。
import type { Snapshot } from "../api/types";

export const VELOCITY_MODES = new Set(["pv", "vm", "csv"]);
export const PANEL_MODES = new Set(["pv", "vm", "csv", "pp", "hm", "csp"]);
export const LEGACY_MODES: Snapshot["availableModes"] = ["pv", "pp"];

export const MODE_LABELS: Record<string, string> = {
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

export const STATE_LABELS: Record<string, string> = {
  STARTING: "启动中",
  SWITCHING: "切换模式",
  ENABLING: "使能中",
  OPERATIONAL: "运行中",
  ENABLED: "已使能",
  JOGGING: "点动中",
  PP_MOVING: "PP 执行中",
  HOMING: "回零中",
  CSP_MOVING: "CSP 执行中",
  WELDING: "焊接中",
  WAITING_INTERFACE: "等待选择网卡",
  ERROR: "故障",
};

export interface Gates {
  hasDrive: boolean;
  hasDigitalIo: boolean;
  hasWelding: boolean;
  canEnable: boolean;
  canSelectAdapter: boolean;
  canSwitchMode: boolean;
  canJog: boolean;
  canMovePp: boolean;
  canHome: boolean;
  canMoveCsp: boolean;
  canControlWelding: boolean;
  canStartWelding: boolean;
  mode: string;
  modeAvailable: boolean;
}

export function computeGates(
  snap: Snapshot | null,
  hasControl: boolean,
  requestedMode: string,
): Gates {
  if (!snap) {
    return {
      hasDrive: false,
      hasDigitalIo: false,
      hasWelding: false,
      canEnable: false,
      canSelectAdapter: false,
      canSwitchMode: false,
      canJog: false,
      canMovePp: false,
      canHome: false,
      canMoveCsp: false,
      canControlWelding: false,
      canStartWelding: false,
      mode: requestedMode,
      modeAvailable: false,
    };
  }
  const hasDrive = snap.hasDrive ?? (snap.deviceType === "drive" || snap.deviceType === "mixed");
  const hasDigitalIo =
    snap.hasDigitalIo ?? (snap.deviceType === "digital_io" || snap.deviceType === "mixed");
  const hasWelding =
    snap.hasWelding ?? (snap.deviceType === "welding" || snap.deviceType === "mixed");
  const mode =
    snap.state === "SWITCHING" ? requestedMode : snap.motionMode ?? "pv";
  const availableModes = Array.isArray(snap.availableModes) ? snap.availableModes : LEGACY_MODES;
  const modeAvailable = availableModes.includes(mode as Snapshot["availableModes"][number]);
  const hasMotion = snap.moving ?? Boolean(
    snap.velocityCommand || snap.ppMoving || snap.homingActive || snap.cspMoving,
  );
  const canEnable =
    hasDrive &&
    hasControl &&
    snap.connected &&
    snap.state !== "ERROR" &&
    snap.state !== "SWITCHING";
  const canSelectAdapter =
    !snap.enableRequested &&
    hasControl &&
    !snap.enabled &&
    !snap.velocityCommand &&
    !hasMotion &&
    !snap.weldingCommandActive &&
    snap.state !== "SWITCHING";
  const canSwitchMode =
    hasDrive &&
    hasControl &&
    snap.connected &&
    !snap.enableRequested &&
    !snap.enabled &&
    snap.state !== "ERROR" &&
    snap.state !== "SWITCHING";
  const canJog =
    hasControl && VELOCITY_MODES.has(mode) && snap.enableRequested && snap.state !== "ERROR";
  const canMovePp =
    mode === "pp" && hasControl && snap.enabled && !snap.ppMoving && snap.state !== "ERROR";
  const canHome =
    mode === "hm" && hasControl && snap.enabled && !snap.homingActive && snap.state !== "ERROR";
  const canMoveCsp =
    mode === "csp" && hasControl && snap.enabled && !snap.cspMoving && snap.state !== "ERROR";
  const weldingConnected = snap.weldingConnected ?? (hasWelding && snap.connected);
  const canControlWelding =
    hasWelding &&
    hasControl &&
    weldingConnected &&
    snap.state !== "ERROR" &&
    snap.state !== "SWITCHING";
  const canStartWelding =
    canControlWelding && Boolean(snap.weldingRobotReady) && !snap.weldingPowerFault;
  return {
    hasDrive,
    hasDigitalIo,
    hasWelding,
    canEnable,
    canSelectAdapter,
    canSwitchMode,
    canJog,
    canMovePp,
    canHome,
    canMoveCsp,
    canControlWelding,
    canStartWelding,
    mode,
    modeAvailable,
  };
}

export function maskToHex(mask: number | string, channels: number): string {
  let value: bigint;
  if (typeof mask === "string") {
    try {
      value = BigInt(mask);
    } catch {
      return "0x0";
    }
  } else {
    const number = Number(mask);
    value = Number.isFinite(number) ? BigInt(Math.trunc(number)) : 0n;
  }
  const width = Math.max(4, Math.ceil(channels / 4));
  return `0x${value.toString(16).padStart(width, "0").toUpperCase()}`;
}

export function channelRange(prefix: string, count: number): string {
  return count > 0
    ? `${prefix} 00 - ${prefix} ${String(count - 1).padStart(2, "0")}`
    : "未识别通道";
}

export function stateKind(state: string): "ok" | "warn" | "err" {
  if (state === "ERROR") return "err";
  if (
    state === "OPERATIONAL" ||
    state === "ENABLED" ||
    state === "JOGGING" ||
    state === "PP_MOVING" ||
    state === "HOMING" ||
    state === "CSP_MOVING" ||
    state === "WELDING"
  ) {
    return "ok";
  }
  return "warn";
}