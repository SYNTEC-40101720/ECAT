from __future__ import annotations

import argparse
import json
import logging
import struct
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pysoem

from .device_profiles import enumerate_adapters, get_drive_profile, resolve_default_interface
from .logging_setup import DEFAULT_LOG_FILE, configure_logging
from .motion_modes import (
    MODE_CSP,
    MODE_CSV,
    MODE_HM,
    MODE_PP,
    MODE_PV,
    MODE_VALUES,
    MODE_VM,
    MOTION_MODES,
    modes_from_capability_word,
)

LOGGER = logging.getLogger("ecat_test.runtime")

CYCLE_US = 10_000
HEARTBEAT_TIMEOUT = 0.35
MAX_VELOCITY = 100_000
DEFAULT_RAMP_TIME = 0.5
MIN_RAMP_TIME = 0.01
MAX_RAMP_TIME = 60.0
MAX_ACCELERATION = 10_000_000
PP_NEW_SETPOINT = 0x0010
PP_RELATIVE = 0x0040
PP_HALT = 0x0100
PP_TARGET_REACHED = 0x0400
HOMING_START = 0x0010
HOMING_ATTAINED = 0x1000
HOMING_ERROR = 0x2000
MIN_POSITION = -(1 << 31)
MAX_POSITION = (1 << 31) - 1
VELOCITY_MODES = {MODE_VM, MODE_PV, MODE_CSV}

# Web HMI asset root and a ring buffer that the SSE stream replays to clients.
WEB_ROOT = Path(__file__).parent / "web"
LOG_BUFFER: deque[str] = deque(maxlen=400)


class _LogCapture(logging.Handler):
    """Append formatted log records into LOG_BUFFER for the SSE stream."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            LOG_BUFFER.append(self.format(record))
        except Exception:  # pragma: no cover - never break the runtime loop
            pass


def packet(
    controlword: int,
    velocity: int,
    acceleration: int = 1000,
    deceleration: int = 1000,
    mode: int = 3,
) -> bytes:
    return struct.pack(
        "<Hiiib", controlword, velocity, acceleration, deceleration, mode
    )


def pp_packet(
    controlword: int,
    target_position: int,
    velocity: int,
    acceleration: int,
    deceleration: int,
    mode: int = 1,
) -> bytes:
    return struct.pack(
        "<HiIIIb",
        controlword,
        target_position,
        velocity,
        acceleration,
        deceleration,
        mode,
    )


def csp_packet(
    controlword: int,
    target_position: int,
    touch_probe: int = 0,
    mode: int = 8,
    mode_in_pdo: bool = True,
    target_velocity: int = 0,
    target_velocity_in_pdo: bool = False,
) -> bytes:
    payload = struct.pack("<HiH", controlword, target_position, touch_probe)
    if mode_in_pdo:
        payload += struct.pack("<b", mode)
    if target_velocity_in_pdo:
        payload += struct.pack("<i", target_velocity)
    return payload


def homing_packet(
    controlword: int,
    method: int,
    fast_velocity: int,
    slow_velocity: int,
    acceleration: int,
    offset: int,
    mode: int = 6,
) -> bytes:
    return struct.pack(
        "<HbIIIib",
        controlword,
        method,
        fast_velocity,
        slow_velocity,
        acceleration,
        offset,
        mode,
    )


class Runtime:
    def __init__(self, interface: str | None) -> None:
        self.interface = interface
        self.master = pysoem.Master()
        self.slave = None
        self.profile = None
        self.mode_capability_word: int | None = None
        self.lock = threading.Lock()
        self.running = bool(interface)
        self.command = 0
        self.heartbeat = 0.0
        self.state = "STARTING" if interface else "WAITING_INTERFACE"
        self.message = "" if interface else "Select an EtherCAT adapter"
        self.statusword = 0
        self.error = 0
        self.mode = 0
        self.wkc = 0
        self.expected_wkc = 0
        self.enabled = False
        self.enable_requested = False
        self.acceleration_time = DEFAULT_RAMP_TIME
        self.deceleration_time = DEFAULT_RAMP_TIME
        self.last_target_velocity = 0
        self.last_logged_command = 0
        self.motion_mode = MODE_PV
        self.pending_mode: str | None = None
        self.pp_target_position = 0
        self.pp_profile_velocity = 1000
        self.pp_acceleration = 1000
        self.pp_deceleration = 1000
        self.pp_relative = False
        self.pp_move_pending = False
        self.pp_move_active = False
        self.pp_trigger = False
        self.pp_move_cycles = 0
        self.pp_halted = True
        self.pp_heartbeat = 0.0
        self.homing_method = 35
        self.homing_fast_velocity = 500
        self.homing_slow_velocity = 100
        self.homing_acceleration = 1000
        self.homing_offset = 0
        self.homing_pending = False
        self.homing_active = False
        self.homing_trigger = False
        self.homing_heartbeat = 0.0
        self.csp_target_position = 0
        self.csp_start_position = 0
        self.csp_duration = DEFAULT_RAMP_TIME
        self.csp_started_at = 0.0
        self.csp_move_pending = False
        self.csp_move_active = False
        self.csp_heartbeat = 0.0
        self.actual_position = 0
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def start(self) -> None:
        if not self.interface:
            with self.lock:
                self.running = False
                self.state = "WAITING_INTERFACE"
                self.message = "Select an EtherCAT adapter"
            return
        if self.thread.is_alive():
            return
        with self.lock:
            self.running = True
        LOGGER.info("Runtime start requested: interface=%s", self.interface)
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def close(self) -> None:
        LOGGER.info("Runtime close requested")
        self.running = False
        if self.thread.is_alive():
            self.thread.join(timeout=2)

    def select_interface(self, interface: str) -> None:
        selected = interface.strip()
        if not selected:
            raise ValueError("interface must not be empty")
        with self.lock:
            if (
                self.enable_requested
                or self.enabled
                or self.command
                or self.pp_move_pending
                or self.pp_move_active
                or self.homing_pending
                or self.homing_active
                or self.csp_move_pending
                or self.csp_move_active
            ):
                raise RuntimeError("disable the drive before changing interface")
            same_active_interface = (
                self.interface == selected and self.thread.is_alive()
            )
        if same_active_interface:
            return
        if self.thread.is_alive():
            self.close()
        if self.thread.is_alive():
            raise RuntimeError("previous EtherCAT runtime is still stopping")
        with self.lock:
            self.master = pysoem.Master()
            self.interface = selected
            self.slave = None
            self.profile = None
            self.mode_capability_word = None
            self.running = False
            self.state = "STARTING"
            self.message = f"Opening EtherCAT interface: {selected}"
            self.statusword = 0
            self.error = 0
            self.mode = 0
            self.wkc = 0
            self.expected_wkc = 0
            self.enabled = False
            self.enable_requested = False
            self.command = 0
            self.heartbeat = 0
            self.last_target_velocity = 0
            self.last_logged_command = 0
            self.motion_mode = MODE_PV
            self.pending_mode = None
            self.pp_move_pending = False
            self.pp_move_active = False
            self.pp_trigger = False
            self.pp_move_cycles = 0
            self.pp_halted = True
            self.pp_heartbeat = 0
            self.homing_pending = False
            self.homing_active = False
            self.homing_trigger = False
            self.homing_heartbeat = 0
            self.csp_target_position = 0
            self.csp_start_position = 0
            self.csp_started_at = 0
            self.csp_move_pending = False
            self.csp_move_active = False
            self.csp_heartbeat = 0
            self.actual_position = 0
        self.start()

    def set_ramp_times(self, acceleration_time: float, deceleration_time: float) -> None:
        self._validate_ramp_time(acceleration_time, "acceleration time")
        self._validate_ramp_time(deceleration_time, "deceleration time")
        with self.lock:
            self.acceleration_time = acceleration_time
            self.deceleration_time = deceleration_time
            LOGGER.info(
                "Ramp times set: acceleration=%.3fs deceleration=%.3fs",
                acceleration_time,
                deceleration_time,
            )

    @staticmethod
    def _validate_ramp_time(value: float, label: str) -> None:
        if not MIN_RAMP_TIME <= value <= MAX_RAMP_TIME:
            raise ValueError(f"{label} must be {MIN_RAMP_TIME}-{MAX_RAMP_TIME}s")

    @staticmethod
    def _ramp_value(magnitude: int, duration: float) -> int:
        return min(MAX_ACCELERATION, max(1, round(max(1, abs(magnitude)) / duration)))

    def ramp_values(
        self,
        velocity: int,
        acceleration_time: float | None = None,
        deceleration_time: float | None = None,
    ) -> tuple[int, int]:
        with self.lock:
            selected_acceleration_time = (
                self.acceleration_time if acceleration_time is None else acceleration_time
            )
            selected_deceleration_time = (
                self.deceleration_time if deceleration_time is None else deceleration_time
            )
            last_target_velocity = self.last_target_velocity
        magnitude = max(1, abs(velocity or last_target_velocity))
        acceleration = self._ramp_value(magnitude, selected_acceleration_time)
        deceleration = self._ramp_value(magnitude, selected_deceleration_time)
        return acceleration, deceleration

    def jog(
        self,
        velocity: int,
        acceleration_time: float | None = None,
        deceleration_time: float | None = None,
    ) -> None:
        if abs(velocity) > MAX_VELOCITY:
            raise ValueError(f"velocity limit is {MAX_VELOCITY}")
        if acceleration_time is not None or deceleration_time is not None:
            self.set_ramp_times(
                acceleration_time if acceleration_time is not None else self.acceleration_time,
                deceleration_time if deceleration_time is not None else self.deceleration_time,
            )
        with self.lock:
            if self.motion_mode not in VELOCITY_MODES:
                raise RuntimeError("Jog is only available in a velocity mode")
            if not self.enable_requested:
                LOGGER.warning("Jog rejected: enable switch is off, velocity=%s", velocity)
                raise RuntimeError("press Enable before Jog")
            self.command = velocity
            self.heartbeat = time.monotonic()
            if velocity:
                self.last_target_velocity = velocity
            if velocity != self.last_logged_command:
                LOGGER.info("Jog command set: velocity=%s", velocity)
                self.last_logged_command = velocity

    def set_mode(self, mode: str) -> None:
        if mode not in MOTION_MODES:
            raise ValueError(f"unsupported motion mode: {mode}")
        with self.lock:
            if self.state == "ERROR":
                raise RuntimeError("drive runtime is in ERROR state")
            if self.profile is not None and mode not in self.available_modes():
                raise RuntimeError(
                    f"{mode.upper()} mode is not available for {self.profile.name}"
                )
            if mode == self.motion_mode and self.pending_mode is None:
                return
            if (
                self.enable_requested
                or self.enabled
                or self.command
                or self.pp_move_pending
                or self.pp_move_active
                or self.homing_pending
                or self.homing_active
                or self.csp_move_pending
                or self.csp_move_active
            ):
                raise RuntimeError("disable the drive before changing mode")
            self.pending_mode = mode
            self.state = "SWITCHING"
            self.message = f"Switching to {mode.upper()} mode..."
            LOGGER.info("Motion mode change requested: %s", mode)

    def move_pp(
        self,
        target_position: int,
        velocity: int,
        acceleration_time: float,
        deceleration_time: float,
        relative: bool = False,
    ) -> None:
        if not MIN_POSITION <= target_position <= MAX_POSITION:
            raise ValueError(f"target position must be {MIN_POSITION}..{MAX_POSITION}")
        if not 1 <= velocity <= MAX_VELOCITY:
            raise ValueError(f"profile velocity must be 1..{MAX_VELOCITY}")
        self._validate_ramp_time(acceleration_time, "acceleration time")
        self._validate_ramp_time(deceleration_time, "deceleration time")
        acceleration, deceleration = self.ramp_values(
            velocity, acceleration_time, deceleration_time
        )
        with self.lock:
            if self.motion_mode != MODE_PP:
                raise RuntimeError("switch to PP mode before positioning")
            if not self.enable_requested or not self.enabled:
                raise RuntimeError("enable the drive before positioning")
            if self.pp_move_pending or self.pp_move_active:
                raise RuntimeError("a PP move is already in progress")
            self.pp_target_position = target_position
            self.pp_profile_velocity = velocity
            self.pp_acceleration = acceleration
            self.pp_deceleration = deceleration
            self.pp_relative = bool(relative)
            self.pp_move_pending = True
            self.pp_move_active = False
            self.pp_trigger = False
            self.pp_move_cycles = 0
            self.pp_halted = False
            self.pp_heartbeat = time.monotonic()
            self.message = "PP move queued"
            LOGGER.info(
                "PP move requested: target=%s velocity=%s accel_time=%.3fs "
                "decel_time=%.3fs acceleration=%s deceleration=%s relative=%s",
                target_position,
                velocity,
                acceleration_time,
                deceleration_time,
                acceleration,
                deceleration,
                relative,
            )

    def start_homing(
        self,
        method: int,
        fast_velocity: int,
        slow_velocity: int,
        acceleration_time: float,
        offset: int = 0,
    ) -> None:
        if not -128 <= method <= 127:
            raise ValueError("homing method must be -128..127")
        if not 1 <= fast_velocity <= MAX_VELOCITY:
            raise ValueError(f"homing fast velocity must be 1..{MAX_VELOCITY}")
        if not 1 <= slow_velocity <= MAX_VELOCITY:
            raise ValueError(f"homing slow velocity must be 1..{MAX_VELOCITY}")
        self._validate_ramp_time(acceleration_time, "homing acceleration time")
        if not MIN_POSITION <= offset <= MAX_POSITION:
            raise ValueError(f"homing offset must be {MIN_POSITION}..{MAX_POSITION}")
        acceleration = self._ramp_value(fast_velocity, acceleration_time)
        with self.lock:
            if self.motion_mode != MODE_HM:
                raise RuntimeError("switch to Homing mode before homing")
            if not self.enable_requested or not self.enabled:
                raise RuntimeError("enable the drive before homing")
            if (
                self.homing_pending
                or self.homing_active
                or self.pp_move_pending
                or self.pp_move_active
                or self.csp_move_pending
                or self.csp_move_active
            ):
                raise RuntimeError("a motion command is already in progress")
            self.homing_method = method
            self.homing_fast_velocity = fast_velocity
            self.homing_slow_velocity = slow_velocity
            self.homing_acceleration = acceleration
            self.homing_offset = offset
            self.homing_pending = True
            self.homing_active = False
            self.homing_trigger = False
            self.homing_heartbeat = time.monotonic()
            self.message = "Homing queued"
            LOGGER.info(
                "Homing requested: method=%s fast_velocity=%s slow_velocity=%s "
                "acceleration_time=%.3fs acceleration=%s offset=%s",
                method,
                fast_velocity,
                slow_velocity,
                acceleration_time,
                acceleration,
                offset,
            )

    def homing_keepalive(self) -> None:
        with self.lock:
            if self.motion_mode == MODE_HM and (
                self.homing_pending or self.homing_active
            ):
                self.homing_heartbeat = time.monotonic()

    def move_csp(self, target_position: int, duration: float) -> None:
        if not MIN_POSITION <= target_position <= MAX_POSITION:
            raise ValueError(f"target position must be {MIN_POSITION}..{MAX_POSITION}")
        self._validate_ramp_time(duration, "CSP move time")
        with self.lock:
            if self.motion_mode != MODE_CSP:
                raise RuntimeError("switch to CSP mode before positioning")
            if not self.enable_requested or not self.enabled:
                raise RuntimeError("enable the drive before positioning")
            if (
                self.csp_move_pending
                or self.csp_move_active
                or self.pp_move_pending
                or self.pp_move_active
                or self.homing_pending
                or self.homing_active
            ):
                raise RuntimeError("a motion command is already in progress")
            self.csp_start_position = self.actual_position
            self.csp_target_position = target_position
            self.csp_duration = duration
            self.csp_started_at = 0.0
            self.csp_move_pending = True
            self.csp_move_active = False
            self.csp_heartbeat = time.monotonic()
            self.message = "CSP move queued"
            LOGGER.info(
                "CSP move requested: target=%s duration=%.3fs start=%s",
                target_position,
                duration,
                self.csp_start_position,
            )

    def csp_keepalive(self) -> None:
        with self.lock:
            if self.motion_mode == MODE_CSP and (
                self.csp_move_pending or self.csp_move_active
            ):
                self.csp_heartbeat = time.monotonic()

    def pp_keepalive(self) -> None:
        with self.lock:
            if self.motion_mode == MODE_PP and (
                self.pp_move_pending or self.pp_move_active
            ):
                self.pp_heartbeat = time.monotonic()

    def enable(self) -> None:
        with self.lock:
            if not self.interface:
                raise RuntimeError("select an EtherCAT adapter first")
            if self.state == "ERROR":
                raise RuntimeError("drive runtime is in ERROR state")
            self.enable_requested = True
            self.state = "ENABLING"
            self.message = "Enabling drive..."
            LOGGER.info("Enable requested")

    def stop_motion(self) -> None:
        with self.lock:
            had_motion = bool(
                self.command
                or self.pp_move_pending
                or self.pp_move_active
                or self.homing_pending
                or self.homing_active
                or self.csp_move_pending
                or self.csp_move_active
            )
            self.command = 0
            self.heartbeat = 0
            self.pp_move_pending = False
            self.pp_move_active = False
            self.pp_trigger = False
            self.pp_halted = self.motion_mode == MODE_PP
            self.pp_heartbeat = 0
            self.homing_pending = False
            self.homing_active = False
            self.homing_trigger = False
            self.homing_heartbeat = 0
            self.csp_move_pending = False
            self.csp_move_active = False
            self.csp_target_position = self.actual_position
            self.csp_start_position = self.actual_position
            self.csp_started_at = 0
            self.csp_heartbeat = 0
            if had_motion or self.last_logged_command != 0:
                LOGGER.info("Motion stopped; enable state preserved")
                self.last_logged_command = 0

    def disable(self) -> None:
        with self.lock:
            self.enable_requested = False
            self.command = 0
            self.heartbeat = 0
            self.last_logged_command = 0
            self.pp_move_pending = False
            self.pp_move_active = False
            self.pp_trigger = False
            self.pp_halted = True
            self.pp_heartbeat = 0
            self.homing_pending = False
            self.homing_active = False
            self.homing_trigger = False
            self.homing_heartbeat = 0
            self.csp_move_pending = False
            self.csp_move_active = False
            self.csp_target_position = self.actual_position
            self.csp_start_position = self.actual_position
            self.csp_started_at = 0
            self.csp_heartbeat = 0
            LOGGER.info("Drive disable requested")

    def stop(self) -> None:
        self.disable()

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            available_modes = self.available_modes()
            target_position = (
                self.csp_target_position
                if self.motion_mode == MODE_CSP
                else self.pp_target_position
            )
            moving = (
                self.pp_move_pending
                or self.pp_move_active
                or self.homing_pending
                or self.homing_active
                or self.csp_move_pending
                or self.csp_move_active
            )
            return {
                "state": self.state,
                "message": self.message,
                "interface": self.interface,
                "device": self.profile.name if self.profile else "",
                "connected": self.state
                in {
                    "OPERATIONAL",
                    "ENABLED",
                    "JOGGING",
                    "PP_MOVING",
                    "HOMING",
                    "CSP_MOVING",
                },
                "enabled": self.enabled,
                "enableRequested": self.enable_requested,
                "motionMode": self.motion_mode,
                "motionModeValue": MODE_VALUES.get(self.motion_mode, 0),
                "availableModes": list(available_modes),
                "modeCapabilities": (
                    f"0x{self.mode_capability_word:04X}"
                    if self.mode_capability_word is not None
                    else ""
                ),
                "velocityCommand": self.command,
                "velocityLimit": MAX_VELOCITY,
                "targetPosition": target_position,
                "actualPosition": self.actual_position,
                "ppMoving": self.pp_move_pending or self.pp_move_active,
                "homingActive": self.homing_pending or self.homing_active,
                "homingAttained": bool(self.statusword & HOMING_ATTAINED),
                "homingError": bool(self.statusword & HOMING_ERROR),
                "cspMoving": self.csp_move_pending or self.csp_move_active,
                "moving": bool(moving or self.command),
                "targetReached": bool(self.statusword & PP_TARGET_REACHED),
                "statusword": f"0x{self.statusword:04X}",
                "errorCode": f"0x{self.error:04X}",
                "mode": self.mode,
                "wkc": self.wkc,
                "expectedWkc": self.expected_wkc,
            }

    def _next_csp_target(self) -> int:
        now = time.monotonic()
        with self.lock:
            if self.csp_move_pending:
                self.csp_move_pending = False
                self.csp_move_active = True
                self.csp_started_at = now
                self.message = "CSP move running"
            if not self.csp_move_active:
                return self.csp_target_position
            elapsed = max(0.0, now - self.csp_started_at)
            progress = min(1.0, elapsed / self.csp_duration)
            target = round(
                self.csp_start_position
                + (self.csp_target_position - self.csp_start_position) * progress
            )
            if progress >= 1.0:
                self.csp_move_active = False
                self.message = "CSP target reached"
            return target

    def cycle(self, controlword: int, velocity: int = 0) -> int:
        mode = self.motion_mode
        mode_pdo = self.profile.mode_pdo(mode) if self.profile else None
        packet_kind = mode_pdo.packet_kind if mode_pdo else "velocity"
        mode_value = MODE_VALUES.get(mode, 3)
        if packet_kind == "profile_position":
            with self.lock:
                target_position = self.pp_target_position
                profile_velocity = self.pp_profile_velocity
                acceleration = self.pp_acceleration
                deceleration = self.pp_deceleration
            self.slave.output = pp_packet(
                controlword,
                target_position,
                profile_velocity,
                acceleration,
                deceleration,
                mode_value,
            )
        elif packet_kind == "velocity":
            ramp_velocity = velocity or self.last_target_velocity
            acceleration, deceleration = self.ramp_values(ramp_velocity)
            self.slave.output = packet(
                controlword, velocity, acceleration, deceleration, mode_value
            )
        elif packet_kind == "homing":
            with self.lock:
                if self.homing_pending:
                    self.homing_pending = False
                    self.homing_active = True
                    self.message = "Homing running"
                method = self.homing_method
                fast_velocity = self.homing_fast_velocity
                slow_velocity = self.homing_slow_velocity
                acceleration = self.homing_acceleration
                offset = self.homing_offset
                homing_active = self.homing_active
            if homing_active:
                controlword |= HOMING_START
            self.slave.output = homing_packet(
                controlword,
                method,
                fast_velocity,
                slow_velocity,
                acceleration,
                offset,
                mode_value,
            )
        elif packet_kind == "csp":
            target_position = self._next_csp_target()
            mode_in_pdo = mode_pdo.mode_in_pdo if mode_pdo else True
            target_velocity_in_pdo = mode_pdo.target_velocity_in_pdo if mode_pdo else False
            self.slave.output = csp_packet(
                controlword,
                target_position,
                mode=mode_value,
                mode_in_pdo=mode_in_pdo,
                target_velocity_in_pdo=target_velocity_in_pdo,
            )
        else:
            raise RuntimeError(f"no packet encoder for {mode.upper()} mode")
        self.master.send_overlap_processdata()
        return self.master.receive_processdata(CYCLE_US)

    def feedback(self) -> None:
        data = bytes(self.slave.input)
        if len(data) >= 5:
            self.error = int.from_bytes(data[0:2], "little")
            self.statusword = int.from_bytes(data[2:4], "little")
            self.mode = struct.unpack_from("<b", data, 4)[0]
        if len(data) >= 9:
            self.actual_position = struct.unpack_from("<i", data, 5)[0]

    def enable_drive(self) -> None:
        LOGGER.info("CiA 402 enable sequence started")
        for controlword in (0x0006, 0x0007, 0x000F):
            for _ in range(5):
                self.wkc = self.cycle(controlword, 0)
                self.feedback()
                if self.wkc != self.expected_wkc:
                    LOGGER.error("Enable WKC mismatch: actual=%s expected=%s", self.wkc, self.expected_wkc)
                    raise RuntimeError(
                        f"enable process-data WKC mismatch: {self.wkc}"
                    )
                time.sleep(CYCLE_US / 1_000_000)
        self.enabled = True
        LOGGER.info("CiA 402 enable sequence completed")

    def mode_settings(self, mode: str) -> tuple[int, int, int]:
        if self.profile is None:
            raise RuntimeError("drive profile is not available")
        if mode not in self.available_modes():
            raise RuntimeError(
                f"{mode.upper()} mode is not available for {self.profile.name}"
            )
        mode_pdo = self.profile.mode_pdo(mode)
        if mode_pdo is None:
            raise RuntimeError(
                f"{mode.upper()} mode is not available for {self.profile.name}"
            )
        return mode_pdo.rx_pdo, mode_pdo.rx_bytes, MODE_VALUES[mode]

    def available_modes(self) -> tuple[str, ...]:
        if self.profile is None:
            return MOTION_MODES
        if self.mode_capability_word is None:
            return self.profile.supported_modes
        return modes_from_capability_word(
            self.mode_capability_word,
            self.profile.supported_modes,
        )

    def _read_mode_capabilities(self) -> None:
        if self.profile is None or self.slave is None:
            raise RuntimeError("drive profile is not available")
        try:
            raw_value = self.slave.sdo_read(0x6502, 0)
        except Exception as exc:
            self.mode_capability_word = None
            raise RuntimeError(
                f"failed to read drive mode capability 0x6502: {exc}"
            )
        if not raw_value:
            raise RuntimeError("drive mode capability 0x6502 returned no data")
        self.mode_capability_word = int.from_bytes(raw_value, "little")
        declared_modes = modes_from_capability_word(
            self.mode_capability_word,
            MOTION_MODES,
        )
        mapped_modes = self.available_modes()
        LOGGER.info(
            "Drive mode capabilities: 0x6502=0x%04X declared=%s mapped=%s",
            self.mode_capability_word,
            ",".join(mode.upper() for mode in declared_modes) or "none",
            ",".join(mode.upper() for mode in mapped_modes) or "none",
        )
        if not mapped_modes:
            raise RuntimeError(
                f"drive {self.profile.name} has no mapped modes enabled by 0x6502"
            )

    def configure_process_data(self, mode: str) -> None:
        rx_pdo, rx_bytes, mode_value = self.mode_settings(mode)
        for index, value in ((0x1C12, rx_pdo), (0x1C13, self.profile.tx_pdo)):
            self.slave.sdo_write(index, 0, b"\x00")
            self.slave.sdo_write(index, 1, value.to_bytes(2, "little"))
            self.slave.sdo_write(index, 0, b"\x01")
        self.slave.sdo_write(0x6040, 0, (0x0080).to_bytes(2, "little"))
        self.slave.sdo_write(
            0x6060, 0, mode_value.to_bytes(1, "little", signed=True)
        )
        self.motion_mode = mode
        io_map_size = self.master.config_overlap_map()
        actual_sizes = (len(self.slave.output), len(self.slave.input))
        expected_sizes = (rx_bytes, self.profile.tx_bytes)
        if actual_sizes != expected_sizes:
            raise RuntimeError(
                f"{self.profile.name} {mode.upper()} expected Rx/Tx bytes "
                f"{expected_sizes[0]}/{expected_sizes[1]}, "
                f"got {actual_sizes[0]}/{actual_sizes[1]}"
            )
        if io_map_size != sum(expected_sizes):
            raise RuntimeError(
                f"unexpected {mode.upper()} process image size: {io_map_size} "
                f"(expected {sum(expected_sizes)})"
            )
        self.expected_wkc = self.master.expected_wkc
        self.pp_halted = mode == MODE_PP
        self.homing_pending = False
        self.homing_active = False
        self.homing_trigger = False
        self.homing_heartbeat = 0
        self.csp_move_pending = False
        self.csp_move_active = False
        self.csp_started_at = 0
        self.csp_heartbeat = 0
        LOGGER.info(
            "PDO configured: device=%s mode=%s rx_pdo=0x%04X io_map=%s bytes "
            "expected_wkc=%s",
            self.profile.name,
            mode.upper(),
            rx_pdo,
            io_map_size,
            self.expected_wkc,
        )
        self.cycle(0x0006, 0)

    def request_operational(self) -> None:
        self.master.state = pysoem.SAFEOP_STATE
        self.master.write_state()
        if self.master.state_check(pysoem.SAFEOP_STATE, 200_000) != pysoem.SAFEOP_STATE:
            raise RuntimeError("drive did not reach SAFE-OP")
        self.master.state = pysoem.OP_STATE
        self.master.write_state()
        if self.master.state_check(pysoem.OP_STATE, 500_000) != pysoem.OP_STATE:
            raise RuntimeError("drive did not reach OP")
        LOGGER.info("EtherCAT reached OP state")

    def configure(self) -> None:
        LOGGER.info("Configuring EtherCAT master")
        if self.master.config_init() != 1:
            raise RuntimeError("expected one EtherCAT slave")
        self.slave = self.master.slaves[0]
        self.profile = get_drive_profile(self.slave.man, self.slave.id)
        if self.profile is None:
            raise RuntimeError(
                "unsupported EtherCAT slave "
                f"0x{self.slave.man:08X}/0x{self.slave.id:08X}"
            )
        self._read_mode_capabilities()
        self.configure_process_data(self.motion_mode)
        self.request_operational()

    def switch_mode(self, mode: str) -> None:
        LOGGER.info("Switching EtherCAT process data to %s mode", mode.upper())
        self.master.state = pysoem.PREOP_STATE
        self.master.write_state()
        if self.master.state_check(pysoem.PREOP_STATE, 500_000) != pysoem.PREOP_STATE:
            raise RuntimeError("drive did not return to PRE-OP for mode switch")
        if self.master.config_init() != 1:
            raise RuntimeError("expected one EtherCAT slave after mode switch")
        self.slave = self.master.slaves[0]
        self.profile = get_drive_profile(self.slave.man, self.slave.id)
        if self.profile is None:
            raise RuntimeError(
                "unsupported EtherCAT slave after mode switch "
                f"0x{self.slave.man:08X}/0x{self.slave.id:08X}"
            )
        self._read_mode_capabilities()
        self.configure_process_data(mode)
        self.request_operational()
        with self.lock:
            self.pending_mode = None
            self.enabled = False
            self.pp_move_pending = False
            self.pp_move_active = False
            self.pp_trigger = False
            self.homing_pending = False
            self.homing_active = False
            self.homing_trigger = False
            self.csp_move_pending = False
            self.csp_move_active = False
            self.state = "OPERATIONAL"
            self.message = f"{mode.upper()} mode ready"
        LOGGER.info("Motion mode switched to %s", mode.upper())

    def loop(self) -> None:
        try:
            self.master.open(self.interface)
            LOGGER.info("EtherCAT interface opened")
            self.configure()
            with self.lock:
                self.state, self.message = "OPERATIONAL", "Ready; hold a Jog button"
            while self.running:
                with self.lock:
                    pending_mode = self.pending_mode
                if pending_mode is not None:
                    self.switch_mode(pending_mode)
                    continue

                with self.lock:
                    command = self.command
                    heartbeat = self.heartbeat
                    motion_mode = self.motion_mode
                    pp_move_pending = self.pp_move_pending
                    pp_move_active = self.pp_move_active
                    pp_heartbeat = self.pp_heartbeat
                    homing_pending = self.homing_pending
                    homing_active = self.homing_active
                    homing_heartbeat = self.homing_heartbeat
                    csp_move_pending = self.csp_move_pending
                    csp_move_active = self.csp_move_active
                    csp_heartbeat = self.csp_heartbeat
                now = time.monotonic()
                if command and now - heartbeat > HEARTBEAT_TIMEOUT:
                    self.stop_motion()
                    command = 0
                    LOGGER.warning("Heartbeat watchdog stopped motion after %.3fs", HEARTBEAT_TIMEOUT)
                    with self.lock:
                        self.message = "Watchdog stopped the drive"
                if (
                    motion_mode == MODE_PP
                    and (pp_move_pending or pp_move_active)
                    and now - pp_heartbeat > HEARTBEAT_TIMEOUT
                ):
                    self.stop_motion()
                    LOGGER.warning(
                        "PP watchdog stopped motion after %.3fs", HEARTBEAT_TIMEOUT
                    )
                    with self.lock:
                        self.message = "PP watchdog stopped the drive"
                if (
                    motion_mode == MODE_HM
                    and (homing_pending or homing_active)
                    and now - homing_heartbeat > HEARTBEAT_TIMEOUT
                ):
                    self.stop_motion()
                    LOGGER.warning(
                        "Homing watchdog stopped motion after %.3fs", HEARTBEAT_TIMEOUT
                    )
                    with self.lock:
                        self.message = "Homing watchdog stopped the drive"
                if (
                    motion_mode == MODE_CSP
                    and (csp_move_pending or csp_move_active)
                    and now - csp_heartbeat > HEARTBEAT_TIMEOUT
                ):
                    self.stop_motion()
                    LOGGER.warning(
                        "CSP watchdog stopped motion after %.3fs", HEARTBEAT_TIMEOUT
                    )
                    with self.lock:
                        self.message = "CSP watchdog stopped the drive"
                with self.lock:
                    enable_requested = self.enable_requested
                pp_trigger_sent = False
                if enable_requested and not self.enabled:
                    self.enable_drive()
                    with self.lock:
                        self.state = "ENABLED"
                        self.message = (
                            "Drive enabled; hold a Jog button"
                            if motion_mode in VELOCITY_MODES
                            else f"Drive enabled; submit a {motion_mode.upper()} command"
                        )
                if not enable_requested:
                    self.wkc = self.cycle(0x0006, 0)
                    self.enabled = False
                elif motion_mode in VELOCITY_MODES and command:
                    self.wkc = self.cycle(0x000F, command)
                elif motion_mode in VELOCITY_MODES:
                    self.wkc = self.cycle(0x000F, 0)
                elif motion_mode == MODE_PP:
                    with self.lock:
                        if self.pp_move_pending:
                            self.pp_move_pending = False
                            self.pp_move_active = True
                            self.pp_trigger = True
                            self.pp_move_cycles = 0
                            self.message = "PP move running"
                        pp_trigger = self.pp_trigger
                        pp_relative = self.pp_relative
                        pp_halted = self.pp_halted
                    controlword = 0x000F
                    if pp_halted:
                        controlword |= PP_HALT
                    if pp_trigger:
                        controlword |= PP_NEW_SETPOINT
                        if pp_relative:
                            controlword |= PP_RELATIVE
                    self.wkc = self.cycle(controlword, 0)
                    if pp_trigger:
                        pp_trigger_sent = True
                        with self.lock:
                            self.pp_trigger = False
                            self.pp_move_cycles = 0
                elif motion_mode == MODE_HM:
                    with self.lock:
                        homing_running = self.homing_pending or self.homing_active
                    controlword = 0x000F
                    if not homing_running:
                        controlword |= PP_HALT
                    self.wkc = self.cycle(controlword, 0)
                elif motion_mode == MODE_CSP:
                    self.wkc = self.cycle(0x000F, 0)
                else:
                    self.wkc = self.cycle(0x000F, 0)
                self.feedback()
                if self.wkc != self.expected_wkc:
                    self.disable()
                    self.enabled = False
                    with self.lock:
                        self.state = "ERROR"
                        self.message = (
                            f"Process-data WKC mismatch: {self.wkc}/"
                            f"{self.expected_wkc}"
                        )
                    LOGGER.error("Process-data WKC mismatch: actual=%s expected=%s", self.wkc, self.expected_wkc)
                    continue
                if motion_mode == MODE_PP:
                    with self.lock:
                        if self.pp_move_active and not pp_trigger_sent and not self.pp_trigger:
                            self.pp_move_cycles += 1
                            if (
                                self.pp_move_cycles >= 2
                                and self.statusword & PP_TARGET_REACHED
                            ):
                                self.pp_move_active = False
                                self.pp_halted = True
                                self.message = "PP target reached"
                if motion_mode == MODE_HM:
                    with self.lock:
                        if self.homing_active and self.statusword & HOMING_ERROR:
                            self.homing_active = False
                            self.enable_requested = False
                            self.enabled = False
                            self.state = "ERROR"
                            self.message = "Homing error reported by drive"
                            LOGGER.error("Drive reported Homing error")
                        elif self.homing_active and self.statusword & HOMING_ATTAINED:
                            self.homing_active = False
                            self.message = "Homing attained"
                with self.lock:
                    if self.state == "ERROR":
                        pass
                    elif self.motion_mode == MODE_PP and self.pp_move_active:
                        self.state = "PP_MOVING"
                    elif self.motion_mode == MODE_HM and self.homing_active:
                        self.state = "HOMING"
                    elif self.motion_mode == MODE_CSP and self.csp_move_active:
                        self.state = "CSP_MOVING"
                    elif self.motion_mode in VELOCITY_MODES and command:
                        self.state = "JOGGING"
                    else:
                        self.state = "ENABLED" if self.enabled else "OPERATIONAL"
                time.sleep(CYCLE_US / 1_000_000)
        except Exception as exc:
            LOGGER.exception("Runtime loop failed")
            with self.lock:
                self.state, self.message = "ERROR", f"{type(exc).__name__}: {exc}"
        finally:
            try:
                if self.slave is not None:
                    self.cycle(0x0006, 0)
            except Exception:
                pass
            self.master.close()
            LOGGER.info("EtherCAT interface closed")


class Handler(BaseHTTPRequestHandler):
    runtime: Runtime
    web_root: Path = WEB_ROOT
    protocol_version = "HTTP/1.1"

    def _send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, rel: str, content_type: str) -> None:
        path = self.web_root / rel
        try:
            data = path.read_bytes()
        except OSError:
            self._send_json({"error": f"asset {rel} missing"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _adapters(self) -> dict:
        try:
            return {
                "adapters": [
                    {"name": name, "desc": description, "selectable": selectable}
                    for name, description, selectable in enumerate_adapters()
                ]
            }
        except Exception as exc:  # pragma: no cover - needs real hardware
            return {"adapters": [], "error": str(exc)}

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route == "/":
            self._serve_file("index.html", "text/html; charset=utf-8")
        elif route == "/styles.css":
            self._serve_file("styles.css", "text/css; charset=utf-8")
        elif route == "/app.js":
            self._serve_file("app.js", "text/javascript; charset=utf-8")
        elif route in ("/api/status", "/api/snapshot"):
            self._send_json(self.runtime.snapshot())
        elif route == "/api/adapters":
            self._send_json(self._adapters())
        elif route == "/api/stream":
            self._stream()
        else:
            self._send_json({"error": "not found"}, 404)

    def _stream(self) -> None:
        """Server-Sent Events: push the live snapshot plus new log lines."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(b": connected\n\n")
        last = len(LOG_BUFFER)
        try:
            while True:
                snap = self.runtime.snapshot()
                self.wfile.write(
                    f"event: state\ndata: {json.dumps(snap, ensure_ascii=False)}\n\n".encode()
                )
                if len(LOG_BUFFER) > last:
                    for line in list(LOG_BUFFER)[last:]:
                        self.wfile.write(
                            f"event: log\ndata: {json.dumps(line, ensure_ascii=False)}\n\n".encode()
                        )
                    last = len(LOG_BUFFER)
                self.wfile.flush()
                time.sleep(0.2)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def do_POST(self) -> None:
        try:
            size = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(size) or b"{}")
            route = urlparse(self.path).path
            if route == "/api/jog":
                LOGGER.info("Browser API jog: velocity=%s", data.get("velocity"))
                self.runtime.jog(int(data["velocity"]))
                self._send_json({"accepted": True})
            elif route == "/api/set_mode":
                LOGGER.info("Browser API set_mode: %s", data.get("mode"))
                self.runtime.set_mode(str(data["mode"]))
                self._send_json({"accepted": True})
            elif route == "/api/move_pp":
                LOGGER.info("Browser API move_pp: %s", data)
                self.runtime.move_pp(
                    int(data["targetPosition"]),
                    int(data["velocity"]),
                    int(data["acceleration"]),
                    int(data["deceleration"]),
                    bool(data.get("relative", False)),
                )
                self._send_json({"accepted": True})
            elif route == "/api/start_homing":
                LOGGER.info("Browser API start_homing: %s", data)
                self.runtime.start_homing(
                    int(data["method"]),
                    int(data["fastVelocity"]),
                    int(data["slowVelocity"]),
                    float(data["accelerationTime"]),
                    int(data.get("offset", 0)),
                )
                self._send_json({"accepted": True})
            elif route == "/api/homing_keepalive":
                self.runtime.homing_keepalive()
                self._send_json({"accepted": True})
            elif route == "/api/move_csp":
                LOGGER.info("Browser API move_csp: %s", data)
                self.runtime.move_csp(
                    int(data["targetPosition"]),
                    float(data["duration"]),
                )
                self._send_json({"accepted": True})
            elif route == "/api/csp_keepalive":
                self.runtime.csp_keepalive()
                self._send_json({"accepted": True})
            elif route == "/api/pp_keepalive":
                self.runtime.pp_keepalive()
                self._send_json({"accepted": True})
            elif route == "/api/stop":
                LOGGER.info("Browser API stop")
                self.runtime.stop()
                self._send_json({"accepted": True})
            elif route == "/api/enable":
                LOGGER.info("Browser API enable")
                self.runtime.enable()
                self._send_json({"accepted": True})
            elif route == "/api/disable":
                LOGGER.info("Browser API disable")
                self.runtime.disable()
                self._send_json({"accepted": True})
            elif route == "/api/select_interface":
                selected = str(data["interface"])
                selectable = {
                    name: is_selectable
                    for name, _description, is_selectable in enumerate_adapters()
                }
                if not selectable.get(selected, False):
                    raise ValueError("select a listed physical EtherCAT adapter")
                LOGGER.info("Browser API select interface: %s", selected)
                self.runtime.select_interface(selected)
                self._send_json({"accepted": True})
            elif route == "/api/set_ramp":
                LOGGER.info("Browser API set_ramp: %s", data)
                self.runtime.set_ramp_times(
                    float(data["acceleration"]), float(data["deceleration"])
                )
                self._send_json({"accepted": True})
            else:
                self._send_json({"error": "not found"}, 404)
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            self._send_json({"accepted": False, "error": str(exc)}, 400)
        except Exception as exc:  # pragma: no cover - defensive HTTP layer
            self._send_json({"accepted": False, "error": f"{type(exc).__name__}: {exc}"}, 500)

    def log_message(self, *_args: object) -> None:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Local ECAT Test HMI")
    parser.add_argument("--interface")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5090)
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_FILE))
    args = parser.parse_args()
    configure_logging(args.log_file)
    LOGGER.info("Starting browser HMI")
    interface = args.interface or resolve_default_interface()
    runtime = Runtime(interface)
    Handler.runtime = runtime
    Handler.web_root = WEB_ROOT

    capture = _LogCapture()
    capture.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.getLogger("ecat_test").addHandler(capture)

    runtime.start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ECAT Test HMI: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        runtime.stop()
        server.server_close()
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
