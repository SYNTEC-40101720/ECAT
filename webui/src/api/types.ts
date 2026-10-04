// Snapshot 契约：desktop/gateway + hmi.Runtime.snapshot() 的镜像。
// 改后端 snapshot 字段时先同步本文件（契约先行）。

export type ConnectionState =
  | "STARTING"
  | "SWITCHING"
  | "ENABLING"
  | "OPERATIONAL"
  | "ENABLED"
  | "JOGGING"
  | "PP_MOVING"
  | "HOMING"
  | "CSP_MOVING"
  | "WELDING"
  | "WAITING_INTERFACE"
  | "ERROR";

export type MotionMode =
  | "pp" | "vm" | "pv" | "pt" | "hm" | "ip" | "csp" | "csv" | "cst";

export type WeldingMode =
  | "dc_unified" | "pulse_unified" | "job" | "remote" | "separate";

export interface Adapter {
  name: string;
  description: string;
  selectable: boolean;
}

export interface Snapshot {
  state: ConnectionState;
  message: string;
  interface: string | null;
  device: string;
  deviceType: string;
  devices: string[];
  hasDrive: boolean;
  hasDigitalIo: boolean;
  hasWelding: boolean;
  driveDevice: string;
  ioDevice: string;
  weldingDevice: string;
  driveConnected: boolean;
  ioConnected: boolean;
  weldingConnected: boolean;
  connected: boolean;
  enabled: boolean;
  enableRequested: boolean;
  motionMode: MotionMode;
  motionModeValue: number;
  availableModes: MotionMode[];
  modeCapabilities: string;
  velocityCommand: number;
  velocityLimit: number;
  cspVelocityLimit: number;
  cspAccelerationLimit: number;
  cspFollowingErrorLimit: number;
  cspCycleTimeLimit: number;
  targetPosition: number;
  actualPosition: number;
  cspFollowingError: number;
  ppMoving: boolean;
  homingActive: boolean;
  homingAttained: boolean;
  homingError: boolean;
  cspMoving: boolean;
  moving: boolean;
  targetReached: boolean;
  statusword: string;
  errorCode: string;
  mode: number;
  wkc: number;
  expectedWkc: number;
  ioInputMask: number;
  ioOutputMask: number;
  ioInputMaskHex: string;
  ioOutputMaskHex: string;
  ioInputChannels: number;
  ioOutputChannels: number;
  weldingCommandActive: boolean;
  weldingStart: boolean;
  weldingRobotReady: boolean;
  weldingMode: WeldingMode;
  weldingModeValue: number;
  weldingGasTest: boolean;
  weldingWireInch: boolean;
  weldingWireRetract: boolean;
  weldingTouchEnable: boolean;
  weldingJob: number;
  weldingCurrentOrSpeed: number;
  weldingVoltageOrStrength: number;
  weldingArcSuccess: boolean;
  weldingActive: boolean;
  weldingPowerFault: boolean;
  weldingCommunicationReady: boolean;
  weldingFaultCode: number;
  weldingTouchSuccess: boolean;
  weldingActualVoltage: number;
  weldingActualCurrent: number;
  weldingWireSpeed: number;
}

export interface AdaptersMessage {
  type: "adapters";
  data: Adapter[];
}

export interface StateMessage {
  type: "state";
  data: Snapshot;
}

export interface ControlMessage {
  type: "control";
  owned?: boolean;
  available?: boolean;
}

export interface LogMessage {
  type: "log";
  data: string;
}

export interface AckMessage {
  type: "ack";
  command: string;
  accepted: boolean;
}

export interface ErrorMessage {
  type: "error";
  error: string;
}

export type ServerMessage =
  | AdaptersMessage
  | StateMessage
  | ControlMessage
  | LogMessage
  | AckMessage
  | ErrorMessage;