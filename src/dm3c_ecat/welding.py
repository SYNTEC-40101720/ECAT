"""Megmeet welding-machine process-data helpers."""

from __future__ import annotations

from dataclasses import dataclass

WELDING_COMMAND_BYTES = 8
WELDING_STATUS_BYTES = 14
WELDING_PROCESS_BYTES = 37

WELDING_MODES = (
    "dc_unified",
    "pulse_unified",
    "job",
    "remote",
    "separate",
)
WELDING_MODE_VALUES = {
    mode: value for value, mode in enumerate(WELDING_MODES)
}


@dataclass(frozen=True, slots=True)
class WeldingStatus:
    arc_success: bool
    welding: bool
    power_fault: bool
    communication_ready: bool
    fault_code: int
    touch_success: bool
    actual_voltage: int
    actual_current: int
    wire_speed: int


def welding_mode_value(mode: str | int) -> int:
    if isinstance(mode, str):
        try:
            return WELDING_MODE_VALUES[mode]
        except KeyError as exc:
            raise ValueError(f"unsupported welding mode: {mode}") from exc
    if isinstance(mode, bool) or mode not in range(len(WELDING_MODES)):
        raise ValueError(f"welding mode must be 0..{len(WELDING_MODES) - 1}")
    return int(mode)


def _require_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean")
    return value


def _require_uint8(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise ValueError(f"{name} must be 0..255")
    return value


def _require_uint16(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFF:
        raise ValueError(f"{name} must be 0..65535")
    return value


def welding_packet(
    *,
    start_welding: bool,
    robot_ready: bool,
    mode: str | int,
    gas_test: bool,
    wire_inch: bool,
    wire_retract: bool,
    touch_enable: bool,
    job: int,
    current_or_speed: int,
    voltage_or_strength: int,
    size: int = WELDING_PROCESS_BYTES,
) -> bytes:
    if size < WELDING_COMMAND_BYTES:
        raise ValueError(f"welding process image must be at least {WELDING_COMMAND_BYTES} bytes")
    mode_value = welding_mode_value(mode)
    job_value = _require_uint8(job, "welding JOB")
    if job_value > 49:
        raise ValueError("welding JOB must be 0..49")
    current_value = _require_uint16(current_or_speed, "welding current or wire speed")
    voltage_value = _require_uint16(voltage_or_strength, "welding voltage or strength")
    command_bits = (
        _require_bool(start_welding, "start welding")
        | (_require_bool(robot_ready, "robot ready") << 1)
        | (mode_value << 2)
    )
    option_bits = (
        _require_bool(gas_test, "gas test")
        | (_require_bool(wire_inch, "wire inch") << 1)
        | (_require_bool(wire_retract, "wire retract") << 2)
        | (_require_bool(touch_enable, "touch enable") << 4)
    )
    payload = bytearray(size)
    payload[0] = command_bits
    payload[1] = option_bits
    payload[2] = job_value
    payload[4:6] = current_value.to_bytes(2, "little")
    payload[6:8] = voltage_value.to_bytes(2, "little")
    return bytes(payload)


def decode_welding_status(data: bytes | bytearray | memoryview) -> WeldingStatus:
    if len(data) < WELDING_STATUS_BYTES:
        raise ValueError(
            f"welding status image must be at least {WELDING_STATUS_BYTES} bytes"
        )
    status_bits = data[0]
    return WeldingStatus(
        arc_success=bool(status_bits & 0x01),
        welding=bool(status_bits & 0x04),
        power_fault=bool(status_bits & 0x20),
        communication_ready=bool(status_bits & 0x40),
        fault_code=data[1],
        touch_success=bool(data[3] & 0x01),
        actual_voltage=int.from_bytes(data[4:6], "little"),
        actual_current=int.from_bytes(data[6:8], "little"),
        wire_speed=int.from_bytes(data[12:14], "little"),
    )
