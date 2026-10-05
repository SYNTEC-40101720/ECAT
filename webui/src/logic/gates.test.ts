// 安全门控纯函数测试：从旧 app.js updateControls() 移植的 can* 组合，
// 逐条锁定“何时允许运动/切换/起焊”的布尔语义。
import { describe, expect, it } from "vitest";
import type { Snapshot } from "../api/types";
import {
  channelRange,
  computeGates,
  maskToHex,
  stateKind,
} from "./gates";

function snapshot(overrides: Partial<Snapshot> = {}): Snapshot {
  return {
    state: "OPERATIONAL",
    message: "",
    interface: "if0",
    device: "Leadshine DM3C-EC556",
    deviceType: "drive",
    devices: ["Leadshine DM3C-EC556"],
    hasDrive: true,
    hasDigitalIo: false,
    hasWelding: false,
    driveDevice: "Leadshine DM3C-EC556",
    ioDevice: "",
    weldingDevice: "",
    driveConnected: true,
    ioConnected: false,
    weldingConnected: false,
    connected: true,
    enabled: false,
    enableRequested: false,
    motionMode: "pv",
    motionModeValue: 3,
    availableModes: ["pv", "pp"],
    modeCapabilities: "0x00A5",
    velocityCommand: 0,
    velocityLimit: 100000,
    cspVelocityLimit: 10000,
    cspAccelerationLimit: 100000,
    cspFollowingErrorLimit: 1000,
    cspCycleTimeLimit: 0.02,
    targetPosition: 0,
    actualPosition: 0,
    cspFollowingError: 0,
    ppMoving: false,
    homingActive: false,
    homingAttained: false,
    homingError: false,
    cspMoving: false,
    moving: false,
    targetReached: false,
    statusword: "0x0000",
    errorCode: "0x0000",
    mode: 0,
    wkc: 3,
    expectedWkc: 3,
    ioInputMask: "0",
    ioOutputMask: "0",
    ioInputMaskHex: "0x0000",
    ioOutputMaskHex: "0x0000",
    ioInputChannels: 0,
    ioOutputChannels: 0,
    weldingCommandActive: false,
    weldingStart: false,
    weldingRobotReady: false,
    weldingMode: "dc_unified",
    weldingModeValue: 0,
    weldingGasTest: false,
    weldingWireInch: false,
    weldingWireRetract: false,
    weldingTouchEnable: false,
    weldingJob: 0,
    weldingCurrentOrSpeed: 0,
    weldingVoltageOrStrength: 0,
    weldingArcSuccess: false,
    weldingActive: false,
    weldingPowerFault: false,
    weldingCommunicationReady: false,
    weldingFaultCode: 0,
    weldingTouchSuccess: false,
    weldingActualVoltage: 0,
    weldingActualCurrent: 0,
    weldingWireSpeed: 0,
    ...overrides,
  } as Snapshot;
}

describe("computeGates", () => {
  it("无 snapshot 时全部门控关闭", () => {
    const gates = computeGates(null, true, "pv");
    expect(gates.canEnable).toBe(false);
    expect(gates.canJog).toBe(false);
    expect(gates.canSwitchMode).toBe(false);
    expect(gates.canMovePp).toBe(false);
  });

  it("无控制权时任何控制门全部关闭", () => {
    const gates = computeGates(snapshot(), false, "pv");
    expect(gates.canEnable).toBe(false);
    expect(gates.canJog).toBe(false);
    expect(gates.canSwitchMode).toBe(false);
    expect(gates.canMovePp).toBe(false);
  });

  it("ERROR 态禁止使能、切换和运动", () => {
    const gates = computeGates(snapshot({ state: "ERROR" }), true, "pv");
    expect(gates.canEnable).toBe(false);
    expect(gates.canSwitchMode).toBe(false);
    expect(gates.canJog).toBe(false);
    expect(gates.canMovePp).toBe(false);
  });

  it("使能链路：连接 + 控制权 + 非 ERROR/SWITCHING 才允许使能", () => {
    expect(computeGates(snapshot(), true, "pv").canEnable).toBe(true);
    expect(computeGates(snapshot({ connected: false }), true, "pv").canEnable).toBe(false);
    expect(computeGates(snapshot({ state: "SWITCHING" }), true, "pv").canEnable).toBe(false);
  });

  it("Jog 只在速度模式且 enableRequested 时允许", () => {
    expect(computeGates(snapshot({ enableRequested: true }), true, "pv").canJog).toBe(true);
    // enableRequested=false（未使能）→ 禁止
    expect(computeGates(snapshot({ enableRequested: false }), true, "pv").canJog).toBe(false);
    // PP 模式（非速度模式）→ 禁止
    expect(
      computeGates(snapshot({ enableRequested: true, motionMode: "pp" }), true, "pp").canJog,
    ).toBe(false);
  });

  it("PP 移动要求已使能且无进行中的 PP 运动", () => {
    const base = { motionMode: "pp" } as Partial<Snapshot>;
    expect(computeGates(snapshot({ ...base, enabled: true }), true, "pp").canMovePp).toBe(true);
    expect(computeGates(snapshot({ ...base, enabled: false }), true, "pp").canMovePp).toBe(false);
    expect(
      computeGates(snapshot({ ...base, enabled: true, ppMoving: true }), true, "pp").canMovePp,
    ).toBe(false);
  });

  it("模式切换要求未使能、未请求使能、已连接", () => {
    expect(computeGates(snapshot(), true, "pv").canSwitchMode).toBe(true);
    expect(
      computeGates(snapshot({ enableRequested: true }), true, "pv").canSwitchMode,
    ).toBe(false);
    expect(computeGates(snapshot({ enabled: true }), true, "pv").canSwitchMode).toBe(false);
    expect(
      computeGates(snapshot({ connected: false }), true, "pv").canSwitchMode,
    ).toBe(false);
  });

  it("网卡切换在使能中、运动中或焊机命令活动时禁止", () => {
    expect(computeGates(snapshot(), true, "pv").canSelectAdapter).toBe(true);
    expect(
      computeGates(snapshot({ enableRequested: true }), true, "pv").canSelectAdapter,
    ).toBe(false);
    expect(
      computeGates(snapshot({ velocityCommand: 500 }), true, "pv").canSelectAdapter,
    ).toBe(false);
    expect(
      computeGates(snapshot({ weldingCommandActive: true }), true, "pv").canSelectAdapter,
    ).toBe(false);
  });

  it("混合总线的 has* 由 deviceType 兜底推导", () => {
    const snap = snapshot({ hasDrive: undefined, deviceType: "mixed", hasDigitalIo: true, hasWelding: true });
    const gates = computeGates(snap, true, "pv");
    expect(gates.hasDrive).toBe(true);
    expect(gates.hasDigitalIo).toBe(true);
    expect(gates.hasWelding).toBe(true);
  });

  it("SWITCHING 态下显示 requestedMode 而非服务器模式", () => {
    const gates = computeGates(
      snapshot({ state: "SWITCHING", motionMode: "pv" }),
      true,
      "csp",
    );
    expect(gates.mode).toBe("csp");
  });

  it("焊机起焊要求 robotReady、通信就绪、无故障码且无电源故障", () => {
    const welding = snapshot({
      hasDrive: false,
      deviceType: "welding",
      hasWelding: true,
      weldingConnected: true,
      weldingRobotReady: true,
      weldingCommunicationReady: true,
    });
    expect(computeGates(welding, true, "pv").canStartWelding).toBe(true);
    expect(
      computeGates(snapshot({ ...welding, weldingPowerFault: true }), true, "pv")
        .canStartWelding,
    ).toBe(false);
    expect(
      computeGates(snapshot({ ...welding, weldingRobotReady: false }), true, "pv")
        .canStartWelding,
    ).toBe(false);
    // 后端 _ensure_welding_start_allowed_locked 同样拒绝通信未就绪与故障码非零
    expect(
      computeGates(snapshot({ ...welding, weldingCommunicationReady: false }), true, "pv")
        .canStartWelding,
    ).toBe(false);
    expect(
      computeGates(snapshot({ ...welding, weldingFaultCode: 5 }), true, "pv")
        .canStartWelding,
    ).toBe(false);
  });

  it("模式切换在运动中、焊机命令活动或数字输出非零时禁止", () => {
    expect(computeGates(snapshot(), true, "pv").canSwitchMode).toBe(true);
    expect(
      computeGates(snapshot({ velocityCommand: 500 }), true, "pv").canSwitchMode,
    ).toBe(false);
    expect(
      computeGates(snapshot({ weldingCommandActive: true }), true, "pv").canSwitchMode,
    ).toBe(false);
    expect(
      computeGates(snapshot({ ioOutputMask: "4" }), true, "pv").canSwitchMode,
    ).toBe(false);
    expect(
      computeGates(snapshot({ ioOutputMask: "0" }), true, "pv").canSwitchMode,
    ).toBe(true);
  });
});

describe("maskToHex / channelRange / stateKind", () => {
  it("按通道数补零到十六进制", () => {
    expect(maskToHex(0, 16)).toBe("0x0000");
    expect(maskToHex(0x8001, 16)).toBe("0x8001");
    expect(maskToHex("0x1", 64)).toBe("0x0000000000000001");
  });

  it("非法字符串回退为 0x0", () => {
    expect(maskToHex("not-a-number", 16)).toBe("0x0");
  });

  it("通道范围文案", () => {
    expect(channelRange("DI", 16)).toBe("DI 00 - DI 15");
    expect(channelRange("DO", 0)).toBe("未识别通道");
  });

  it("状态分类", () => {
    expect(stateKind("OPERATIONAL")).toBe("ok");
    expect(stateKind("ERROR")).toBe("err");
    expect(stateKind("STARTING")).toBe("warn");
  });
});