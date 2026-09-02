from __future__ import annotations

import logging
import struct
import threading
import time
from collections import deque

import pysoem

from .device_profiles import (
    enumerate_adapters,
    get_drive_profile,
    get_remote_io_profile,
    get_welding_profile,
    initialize_remote_io_modules,
    is_tolerated_mapping_error,
    resolve_default_interface,
)
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
from .welding import (
    WELDING_MODES,
    decode_welding_status,
    welding_mode_value,
    welding_packet,
)

LOGGER = logging.getLogger("ecat_test.runtime")

CYCLE_US = 10_000
HEARTBEAT_TIMEOUT = 0.35
MAX_VELOCITY = 100_000
DEFAULT_RAMP_TIME = 0.5
MIN_RAMP_TIME = 0.01
MAX_RAMP_TIME = 60.0
MAX_ACCELERATION = 10_000_000
CSP_MAX_VELOCITY = 10_000
CSP_MAX_ACCELERATION = 100_000
CSP_MAX_FOLLOWING_ERROR = 1_000
CSP_MAX_CYCLE_TIME = 0.020
PP_NEW_SETPOINT = 0x0010
PP_RELATIVE = 0x0040
PP_HALT = 0x0100
PP_TARGET_REACHED = 0x0400
HOMING_START = 0x0010
HOMING_ATTAINED = 0x1000
HOMING_ERROR = 0x2000
CIA402_STATE_MASK = 0x006F
CIA402_FAULT = 0x0008
CIA402_ENABLE_STEPS = (
    (0x0006, 0x0021, "Ready to switch on"),
    (0x0007, 0x0023, "Switched on"),
    (0x000F, 0x0027, "Operation enabled"),
)
CIA402_STATE_RETRIES = 100
MIN_POSITION = -(1 << 31)
MAX_POSITION = (1 << 31) - 1
VELOCITY_MODES = {MODE_VM, MODE_PV, MODE_CSV}

# A ring buffer that the WebSocket gateway replays to clients.
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
        self.drive_slave = None
        self.io_slave = None
        self.welding_slave = None
        self.profile = None
        self.io_profile = None
        self.welding_profile = None
        self.io_input_mask = 0
        self.io_output_mask = 0
        self.welding_command_active = False
        self.welding_start_welding = False
        self.welding_robot_ready = False
        self.welding_mode = WELDING_MODES[0]
        self.welding_gas_test = False
        self.welding_wire_inch = False
        self.welding_wire_retract = False
        self.welding_touch_enable = False
        self.welding_job = 0
        self.welding_current_or_speed = 0
        self.welding_voltage_or_strength = 0
        self.welding_heartbeat = 0.0
        self.welding_arc_success = False
        self.welding_active = False
        self.welding_power_fault = False
        self.welding_communication_ready = False
        self.welding_fault_code = 0
        self.welding_touch_success = False
        self.welding_actual_voltage = 0
        self.welding_actual_current = 0
        self.welding_wire_speed = 0
        self.mode_capability_word: int | None = None
        self.lock = threading.Lock()
        self.process_data_lock = threading.Lock()
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
        self._master_open = False
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
        self.csp_command_position: int | None = None
        self.csp_last_cycle_at: float | None = None
        self.csp_protection_active = False
        self.csp_following_error = 0
        self.actual_position = 0
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def _clear_welding_command_locked(self) -> None:
        self.welding_command_active = False
        self.welding_start_welding = False
        self.welding_robot_ready = False
        self.welding_gas_test = False
        self.welding_wire_inch = False
        self.welding_wire_retract = False
        self.welding_touch_enable = False
        self.welding_heartbeat = 0.0

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
        self.stop()
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
                or self.welding_command_active
                or self.welding_active
                or self.io_output_mask
            ):
                raise RuntimeError(
                    "disable the drive or clear outputs before changing interface"
                )
            same_active_interface = (
                self.interface == selected and self.thread.is_alive()
            )
        if same_active_interface:
            return
        self._prepare_bus_switch("interface change")
        if self.thread.is_alive():
            self.close()
        if self.thread.is_alive():
            raise RuntimeError("previous EtherCAT runtime is still stopping")
        with self.lock:
            self.master = pysoem.Master()
            self._master_open = False
            self.interface = selected
            self.slave = None
            self.drive_slave = None
            self.io_slave = None
            self.welding_slave = None
            self.profile = None
            self.io_profile = None
            self.welding_profile = None
            self.io_input_mask = 0
            self.io_output_mask = 0
            self._clear_welding_command_locked()
            self.welding_arc_success = False
            self.welding_active = False
            self.welding_power_fault = False
            self.welding_communication_ready = False
            self.welding_fault_code = 0
            self.welding_touch_success = False
            self.welding_actual_voltage = 0
            self.welding_actual_current = 0
            self.welding_wire_speed = 0
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
    def _validate_csp_trajectory(
        start_position: int, target_position: int, duration: float
    ) -> tuple[int, int]:
        displacement = abs(target_position - start_position)
        derived_velocity = round(displacement / duration)
        derived_acceleration = round(derived_velocity / duration)
        if derived_velocity > CSP_MAX_VELOCITY:
            raise ValueError(
                f"CSP trajectory velocity limit is {CSP_MAX_VELOCITY}"
            )
        if derived_acceleration > CSP_MAX_ACCELERATION:
            raise ValueError(
                f"CSP trajectory acceleration limit is {CSP_MAX_ACCELERATION}"
            )
        return derived_velocity, derived_acceleration

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
        if self.profile is None:
            raise RuntimeError("no drive is connected for motion commands")
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
        if self.profile is None and (self.io_profile is not None or self.welding_profile is not None):
            raise RuntimeError("connected non-motion device has no motion modes")
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
                or self.welding_command_active
                or self.welding_active
                or self.io_output_mask
            ):
                raise RuntimeError("disable the drive before changing mode")
            self.pending_mode = mode
            self.state = "SWITCHING"
            self.message = f"Switching to {mode.upper()} mode..."
            LOGGER.info("Motion mode change requested: %s", mode)

    def _prepare_bus_switch(self, operation: str) -> None:
        """Verify and transmit a strict all-zero frame before bus reconfiguration."""
        with self.lock:
            if self.enable_requested or self.enabled:
                blocked = "drive enabled"
            elif (
                self.command
                or self.pp_move_pending
                or self.pp_move_active
                or self.homing_pending
                or self.homing_active
                or self.csp_move_pending
                or self.csp_move_active
            ):
                blocked = "motion command or feedback active"
            elif self.welding_command_active or self.welding_active:
                blocked = "welding command or arc feedback active"
            elif self.io_output_mask:
                blocked = "digital outputs are non-zero"
            else:
                blocked = None
            if blocked:
                raise RuntimeError(f"cannot perform {operation}: {blocked}")

            if not any(
                device is not None
                for device in (self.profile, self.io_profile, self.welding_profile)
            ):
                return
            self.io_output_mask = 0
            self._clear_welding_command_locked()

        if not self._master_open:
            return
        try:
            if self.profile is not None and self._drive_device() is not None:
                self.wkc = self.cycle(0x0006, 0)
            elif self.welding_profile is not None and self._welding_device() is not None:
                self.wkc = self.welding_cycle()
            elif self.io_profile is not None and self._io_device() is not None:
                self.wkc = self.io_cycle()
            else:
                return
            if self.wkc != self.expected_wkc:
                raise RuntimeError(
                    f"switch safety-frame WKC mismatch: {self.wkc}/{self.expected_wkc}"
                )
        except Exception as exc:
            message = f"{operation} safety interlock failed: {exc}"
            self._latch_runtime_error(message)
            LOGGER.error(message)
            raise RuntimeError(message) from exc

    def move_pp(
        self,
        target_position: int,
        velocity: int,
        acceleration_time: float,
        deceleration_time: float,
        relative: bool = False,
    ) -> None:
        if self.profile is None and (self.io_profile is not None or self.welding_profile is not None):
            raise RuntimeError("connected non-motion device has no motion commands")
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
        if self.profile is None and (self.io_profile is not None or self.welding_profile is not None):
            raise RuntimeError("connected non-motion device has no motion commands")
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
        if self.profile is None and (self.io_profile is not None or self.welding_profile is not None):
            raise RuntimeError("connected non-motion device has no motion commands")
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
            start_position = self.actual_position
            derived_velocity, derived_acceleration = self._validate_csp_trajectory(
                start_position, target_position, duration
            )
            self.csp_start_position = start_position
            self.csp_target_position = target_position
            self.csp_duration = duration
            self.csp_started_at = 0.0
            self.csp_move_pending = True
            self.csp_move_active = False
            self.csp_command_position = start_position
            self.csp_last_cycle_at = None
            self.csp_protection_active = False
            self.csp_following_error = 0
            self.csp_heartbeat = time.monotonic()
            self.message = "CSP move queued"
            LOGGER.info(
                "CSP move requested: target=%s duration=%.3fs start=%s "
                "derived_velocity=%s derived_acceleration=%s",
                target_position,
                duration,
                start_position,
                derived_velocity,
                derived_acceleration,
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
            if self.profile is None:
                raise RuntimeError("no drive is connected for drive enable")
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
                or self.welding_command_active
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
            outputs_were_set = self.io_output_mask != 0
            welding_was_active = self.welding_command_active
            self._clear_welding_command_locked()
            if self.io_profile is not None:
                self.io_output_mask = 0
            if had_motion or self.last_logged_command != 0:
                LOGGER.info("Motion stopped; enable state preserved")
                self.last_logged_command = 0
            if outputs_were_set:
                LOGGER.info("Digital outputs cleared")
            if welding_was_active:
                LOGGER.info("Welding command cleared")
        self._transmit_safe_outputs(disable_drive=False)

    def disable(self) -> None:
        with self.lock:
            self.enable_requested = False
            self.enabled = False
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
            self._clear_welding_command_locked()
            self.io_output_mask = 0
            LOGGER.info("Drive disable requested")
        self._transmit_safe_outputs(disable_drive=True)

    def stop(self) -> None:
        self.disable()

        with self.lock:
            if self.io_profile is not None:
                self.io_output_mask = 0
                self.message = "Digital outputs cleared"
            if self.welding_profile is not None:
                self._clear_welding_command_locked()
                self.message = "Welding command cleared"

    def _transmit_safe_outputs(self, *, disable_drive: bool) -> None:
        if not self._master_open:
            LOGGER.debug("Skipping safe outputs: EtherCAT interface is not open")
            return
        try:
            validate_wkc = False
            pure_io = False
            if self.profile is not None and self._drive_device() is not None:
                controlword = 0x0006 if disable_drive else 0x000F
                self.wkc = self.cycle(controlword, 0)
                validate_wkc = True
            elif self.welding_profile is not None and self._welding_device() is not None:
                self.wkc = self.welding_cycle()
                validate_wkc = True
            elif self.io_profile is not None and self._io_device() is not None:
                self.wkc = self.io_cycle()
                validate_wkc = True
                pure_io = True
            if validate_wkc and not self._process_wkc_is_valid(pure_io=pure_io):
                raise RuntimeError(
                    f"safe process-data WKC mismatch: {self.wkc}/{self.expected_wkc}"
                )
        except Exception as exc:
            LOGGER.exception("Failed to transmit safe process-data outputs")
            detail = (
                "Safe output transmission failed: "
                f"{type(exc).__name__}: {exc}"
            )
            with self.lock:
                self.running = False
                self.state = "ERROR"
                self.message = f"{self.message}; {detail}" if self.message else detail

    def set_digital_output(self, channel: int, enabled: bool) -> None:
        if not isinstance(channel, int):
            raise ValueError("digital output channel must be an integer")
        if not isinstance(enabled, bool):
            raise ValueError("digital output value must be boolean")
        with self.lock:
            if self.io_profile is None:
                raise RuntimeError("digital I/O device is not connected")
            if not 0 <= channel < self.io_profile.output_channels:
                raise ValueError(
                    f"digital output channel must be 0..{self.io_profile.output_channels - 1}"
                )
            if self.state not in {
                "OPERATIONAL",
                "ENABLED",
                "JOGGING",
                "PP_MOVING",
                "HOMING",
                "CSP_MOVING",
                "WELDING",
            }:
                raise RuntimeError("digital I/O is not operational")
            bit = 1 << channel
            self.io_output_mask = (
                self.io_output_mask | bit
                if enabled
                else self.io_output_mask & ~bit
            )
            LOGGER.info(
                "Digital output changed: channel=%02d enabled=%s mask=0x%04X",
                channel,
                enabled,
                self.io_output_mask,
            )

    def set_welding_command(
        self,
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
    ) -> None:
        if self.welding_profile is None:
            raise RuntimeError("Megmeet welding machine is not connected")
        if start_welding:
            raise ValueError("use start_welding after setting welding parameters")
        mode_value = welding_mode_value(mode)
        welding_packet(
            start_welding=start_welding,
            robot_ready=robot_ready,
            mode=mode_value,
            gas_test=gas_test,
            wire_inch=wire_inch,
            wire_retract=wire_retract,
            touch_enable=touch_enable,
            job=job,
            current_or_speed=current_or_speed,
            voltage_or_strength=voltage_or_strength,
            size=self.welding_profile.rx_bytes,
        )
        with self.lock:
            if self.state not in {
                "OPERATIONAL",
                "ENABLED",
                "JOGGING",
                "PP_MOVING",
                "HOMING",
                "CSP_MOVING",
                "WELDING",
            }:
                raise RuntimeError("welding device is not operational")
            self.welding_command_active = True
            self.welding_start_welding = start_welding
            self.welding_robot_ready = robot_ready
            self.welding_mode = WELDING_MODES[mode_value]
            self.welding_gas_test = gas_test
            self.welding_wire_inch = wire_inch
            self.welding_wire_retract = wire_retract
            self.welding_touch_enable = touch_enable
            self.welding_job = job
            self.welding_current_or_speed = current_or_speed
            self.welding_voltage_or_strength = voltage_or_strength
            self.welding_heartbeat = time.monotonic()
            self.message = "Welding command active"
            LOGGER.info(
                "Welding command set: start=%s ready=%s mode=%s job=%s "
                "current_or_speed=%s voltage_or_strength=%s",
                start_welding,
                robot_ready,
                self.welding_mode,
                job,
                current_or_speed,
                voltage_or_strength,
            )

    def start_welding(self) -> None:
        with self.lock:
            if self.welding_profile is None:
                raise RuntimeError("Megmeet welding machine is not connected")
            if self.state not in {
                "OPERATIONAL",
                "ENABLED",
                "JOGGING",
                "PP_MOVING",
                "HOMING",
                "CSP_MOVING",
                "WELDING",
            }:
                raise RuntimeError("welding device is not operational")
            if not self.welding_robot_ready:
                raise RuntimeError("set robot ready before starting welding")
            if not self.welding_command_active:
                raise RuntimeError("set welding parameters before starting welding")
            self._ensure_welding_start_allowed_locked(self.welding_robot_ready)
            self.welding_command_active = True
            self.welding_start_welding = True
            self.welding_heartbeat = time.monotonic()
            self.message = "Welding start requested"
            LOGGER.info("Welding start requested")

    def _ensure_welding_start_allowed_locked(self, robot_ready: bool) -> None:
        if not robot_ready:
            raise RuntimeError("set robot ready before starting welding")
        if not self.welding_communication_ready:
            raise RuntimeError("welding communication is not ready")
        if self.welding_power_fault:
            raise RuntimeError("welding power reports a fault")
        if self.welding_fault_code:
            raise RuntimeError(
                f"welding machine reports fault code {self.welding_fault_code}"
            )

    def stop_welding(self) -> None:
        with self.lock:
            had_command = self.welding_command_active or self.welding_start_welding
            self._clear_welding_command_locked()
            if had_command:
                self.message = "Welding command stopped"
                LOGGER.info("Welding command stopped")
        self._write_welding_command()

    def welding_keepalive(self) -> None:
        with self.lock:
            if self.welding_command_active:
                self.welding_heartbeat = time.monotonic()

    def _drive_device(self):
        return self.drive_slave or (
            self.slave if self.profile is not None else None
        )

    def _welding_device(self):
        return self.welding_slave or (
            self.slave if self.welding_profile is not None else None
        )

    def _io_device(self):
        return self.io_slave or (
            self.slave if self.io_profile is not None else None
        )

    def _expected_process_image_size(self, drive_rx_bytes: int | None = None) -> int:
        total = 0
        if self.profile is not None:
            selected_rx_bytes = drive_rx_bytes
            if selected_rx_bytes is None:
                mode_pdo = self.profile.mode_pdo(self.motion_mode)
                selected_rx_bytes = (
                    mode_pdo.rx_bytes if mode_pdo is not None else self.profile.rx_bytes
                )
            total += selected_rx_bytes + self.profile.tx_bytes
        if self.io_profile is not None:
            total += self.io_profile.io_map_bytes
        if self.welding_profile is not None:
            total += self.welding_profile.io_map_bytes
        return total

    def _write_io_output(self) -> None:
        io_slave = self._io_device()
        if self.io_profile is None or io_slave is None:
            return
        with self.lock:
            output_mask = self.io_output_mask
        offset = self.io_profile.coupler_rx_bytes
        payload = output_mask.to_bytes(self.io_profile.rx_bytes - offset, "little")
        if offset:
            io_slave.output = b"\x00" * offset + payload
        else:
            io_slave.output = payload

    def _write_welding_command(self) -> None:
        welding_slave = self._welding_device()
        if self.welding_profile is None or welding_slave is None:
            return
        with self.lock:
            command_active = self.welding_command_active
            start_welding = self.welding_start_welding if command_active else False
            robot_ready = self.welding_robot_ready if command_active else False
            mode = self.welding_mode
            gas_test = self.welding_gas_test if command_active else False
            wire_inch = self.welding_wire_inch if command_active else False
            wire_retract = self.welding_wire_retract if command_active else False
            touch_enable = self.welding_touch_enable if command_active else False
            job = self.welding_job if command_active else 0
            current_or_speed = self.welding_current_or_speed if command_active else 0
            voltage_or_strength = self.welding_voltage_or_strength if command_active else 0
            size = self.welding_profile.rx_bytes
        if not command_active:
            welding_slave.output = bytes(size)
            return
        welding_slave.output = welding_packet(
            start_welding=start_welding,
            robot_ready=robot_ready,
            mode=mode,
            gas_test=gas_test,
            wire_inch=wire_inch,
            wire_retract=wire_retract,
            touch_enable=touch_enable,
            job=job,
            current_or_speed=current_or_speed,
            voltage_or_strength=voltage_or_strength,
            size=size,
        )

    def _send_process_data(self) -> None:
        if self.profile is not None or (
            self.welding_profile is not None and self.io_profile is not None
        ):
            self.master.send_overlap_processdata()
        else:
            self.master.send_processdata()

    def io_cycle(self) -> int:
        with self.process_data_lock:
            return self._io_cycle_unlocked()

    def _io_cycle_unlocked(self) -> int:
        if self.io_profile is None or self._io_device() is None:
            raise RuntimeError("digital I/O process data is not configured")
        self._write_io_output()
        self._write_welding_command()
        self._send_process_data()
        return self.master.receive_processdata(CYCLE_US)

    def welding_cycle(self) -> int:
        with self.process_data_lock:
            return self._welding_cycle_unlocked()

    def _welding_cycle_unlocked(self) -> int:
        if self.welding_profile is None or self._welding_device() is None:
            raise RuntimeError("welding process data is not configured")
        self._write_welding_command()
        self._write_io_output()
        self._send_process_data()
        return self.master.receive_processdata(CYCLE_US)

    def read_io_inputs(self) -> int:
        io_slave = self._io_device()
        if self.io_profile is None or io_slave is None:
            raise RuntimeError("digital I/O process data is not configured")
        offset = self.io_profile.coupler_tx_bytes
        payload_bytes = self.io_profile.tx_bytes - offset
        input_mask = int.from_bytes(
            bytes(io_slave.input[offset : self.io_profile.tx_bytes]), "little"
        )
        if payload_bytes > 0:
            input_mask &= (1 << self.io_profile.input_channels) - 1
        with self.lock:
            self.io_input_mask = input_mask
        return input_mask

    def welding_feedback(self) -> None:
        welding_slave = self._welding_device()
        if welding_slave is None:
            return
        status = decode_welding_status(bytes(welding_slave.input))
        stop_required = False
        with self.lock:
            self.welding_arc_success = status.arc_success
            self.welding_active = status.welding
            self.welding_power_fault = status.power_fault
            self.welding_communication_ready = status.communication_ready
            self.welding_fault_code = status.fault_code
            self.welding_touch_success = status.touch_success
            self.welding_actual_voltage = status.actual_voltage
            self.welding_actual_current = status.actual_current
            self.welding_wire_speed = status.wire_speed
            if self.welding_start_welding and (
                not status.communication_ready
                or status.power_fault
                or status.fault_code != 0
            ):
                self._clear_welding_command_locked()
                self.message = "Welding interlock lost; command stopped"
                stop_required = True
        if stop_required:
            self._write_welding_command()
            LOGGER.error(
                "Welding interlock lost: communication_ready=%s "
                "power_fault=%s fault_code=%s",
                status.communication_ready,
                status.power_fault,
                status.fault_code,
            )

    def _prepare_io_modules(self) -> None:
        io_slave = self._io_device()
        if self.io_profile is None or io_slave is None:
            return
        if (
            not self.io_profile.module_init_commands
            and self.io_profile.module_config_protocol != "f030_array"
        ):
            return

        self.master.read_state()
        self.master.state = pysoem.PREOP_STATE
        self.master.write_state()
        reached = self.master.state_check(pysoem.PREOP_STATE, 200_000)
        if reached != pysoem.PREOP_STATE:
            raise RuntimeError(
                "digital I/O device did not reach PRE-OP for module initialization"
            )
        self.io_profile = initialize_remote_io_modules(io_slave, self.io_profile)
        LOGGER.info(
            "Digital I/O modules initialized: device=%s modules=%s",
            self.io_profile.name,
            ",".join(f"0x{module_id:08X}" for module_id in self.io_profile.expected_module_ids),
        )

    def _map_process_data(self, *, overlap: bool) -> int:
        mapper = self.master.config_overlap_map if overlap else self.master.config_map
        try:
            return mapper()
        except pysoem.ConfigMapError as exc:
            io_slave = self._io_device()
            io_profile = self.io_profile
            if io_slave is None or io_profile is None:
                raise
            slave_position = next(
                (
                    index
                    for index, candidate in enumerate(self.master.slaves, start=1)
                    if candidate is io_slave
                ),
                None,
            )
            errors = getattr(exc, "error_list", ())
            if (
                slave_position is None
                or not errors
                or not all(
                    is_tolerated_mapping_error(
                        error,
                        io_profile,
                        slave_position,
                    )
                    for error in errors
                )
            ):
                raise
            mapped_size = sum(
                len(candidate.output) + len(candidate.input)
                for candidate in self.master.slaves
            )
            LOGGER.warning(
                "Ignoring known fixed-PDO mapping SDO error for %s at slave %s; "
                "validated mapped process image size=%s bytes",
                io_profile.name,
                slave_position,
                mapped_size,
            )
            return mapped_size

    def configure_io_process_data(self) -> None:
        io_slave = self._io_device()
        if self.io_profile is None or io_slave is None:
            raise RuntimeError("digital I/O profile is not available")
        self._prepare_io_modules()
        io_map_size = self._map_process_data(
            overlap=self.profile is not None or self.welding_profile is not None
        )
        self._validate_io_process_data()
        self._validate_welding_process_data()
        if io_map_size != self._expected_process_image_size():
            raise RuntimeError(
                f"unexpected digital I/O process image size: {io_map_size} "
                f"(expected {self._expected_process_image_size()})"
            )
        with self.lock:
            self.io_input_mask = 0
            self.io_output_mask = 0
        self.expected_wkc = self.master.expected_wkc
        LOGGER.info(
            "Digital I/O PDO configured: device=%s rx_pdo=0x%04X tx_pdo=0x%04X "
            "io_map=%s bytes expected_wkc=%s",
            self.io_profile.name,
            self.io_profile.rx_pdo,
            self.io_profile.tx_pdo,
            io_map_size,
            self.expected_wkc,
        )

    def configure_welding_process_data(self) -> None:
        welding_slave = self._welding_device()
        if self.welding_profile is None or welding_slave is None:
            raise RuntimeError("welding profile is not available")
        io_map_size = self._map_process_data(
            overlap=self.profile is not None or self.io_profile is not None
        )
        self._validate_welding_process_data()
        if self.io_profile is not None:
            self._validate_io_process_data()
        if io_map_size != self._expected_process_image_size():
            raise RuntimeError(
                f"unexpected welding process image size: {io_map_size} "
                f"(expected {self._expected_process_image_size()})"
            )
        with self.lock:
            self._clear_welding_command_locked()
            self.io_input_mask = 0
            self.io_output_mask = 0
            self.welding_arc_success = False
            self.welding_active = False
            self.welding_power_fault = False
            self.welding_communication_ready = False
            self.welding_fault_code = 0
            self.welding_touch_success = False
            self.welding_actual_voltage = 0
            self.welding_actual_current = 0
            self.welding_wire_speed = 0
        self.expected_wkc = self.master.expected_wkc
        LOGGER.info(
            "Welding PDO configured: device=%s rx_pdo=0x%04X tx_pdo=0x%04X "
            "io_map=%s bytes expected_wkc=%s",
            self.welding_profile.name,
            self.welding_profile.rx_pdo,
            self.welding_profile.tx_pdo,
            io_map_size,
            self.expected_wkc,
        )

    def _validate_io_process_data(self) -> None:
        io_slave = self._io_device()
        if self.io_profile is None or io_slave is None:
            raise RuntimeError("digital I/O profile is not available")
        actual_sizes = (len(io_slave.output), len(io_slave.input))
        expected_sizes = (self.io_profile.rx_bytes, self.io_profile.tx_bytes)
        if actual_sizes != expected_sizes:
            raise RuntimeError(
                f"{self.io_profile.name} expected Rx/Tx bytes "
                f"{expected_sizes[0]}/{expected_sizes[1]}, "
                f"got {actual_sizes[0]}/{actual_sizes[1]}"
            )

    def _validate_welding_process_data(self) -> None:
        welding_slave = self._welding_device()
        if self.welding_profile is None or welding_slave is None:
            return
        actual_sizes = (len(welding_slave.output), len(welding_slave.input))
        expected_sizes = (self.welding_profile.rx_bytes, self.welding_profile.tx_bytes)
        if actual_sizes != expected_sizes:
            raise RuntimeError(
                f"{self.welding_profile.name} expected Rx/Tx bytes "
                f"{expected_sizes[0]}/{expected_sizes[1]}, "
                f"got {actual_sizes[0]}/{actual_sizes[1]}"
            )

    @staticmethod
    def _read_pdo_assignment(slave: object, index: int, expected: int) -> None:
        count = int.from_bytes(slave.sdo_read(index, 0), "little")
        if count < 1:
            raise RuntimeError(f"PDO assignment 0x{index:04X}:00 is empty")
        assigned = int.from_bytes(slave.sdo_read(index, 1), "little")
        if assigned != expected:
            raise RuntimeError(
                f"PDO assignment 0x{index:04X}:01 mismatch: "
                f"expected 0x{expected:04X}, got 0x{assigned:04X}"
            )

    def _validate_drive_pdo_assignments(self, rx_pdo: int) -> None:
        drive_slave = self._drive_device()
        if drive_slave is None:
            raise RuntimeError("drive process data is not configured")
        self._read_pdo_assignment(drive_slave, 0x1C12, rx_pdo)
        self._read_pdo_assignment(drive_slave, 0x1C13, self.profile.tx_pdo)

    def _process_wkc_is_valid(self, *, pure_io: bool = False) -> bool:
        if (
            pure_io
            and self.io_profile is not None
            and self.io_profile.allow_partial_wkc
        ):
            return self.wkc > 0
        return self.wkc == self.expected_wkc

    def _latch_runtime_error(self, message: str) -> None:
        with self.lock:
            self.running = False
            self.enable_requested = False
            self.enabled = False
            self.command = 0
            self.heartbeat = 0
            self.last_target_velocity = 0
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
            self.csp_heartbeat = 0
            self.csp_command_position = None
            self.csp_last_cycle_at = None
            self.csp_protection_active = False
            self.csp_following_error = 0
            self.io_output_mask = 0
            self._clear_welding_command_locked()
            self.state = "ERROR"
            self.message = message

    def run_io_loop(self) -> None:
        while self.running:
            self.wkc = self.io_cycle()
            self.read_io_inputs()
            if not self._process_wkc_is_valid(pure_io=True):
                if self.wkc <= 0:
                    message = (
                        f"Digital I/O process-data exchange lost: WKC={self.wkc}"
                    )
                    self._latch_runtime_error(message)
                    LOGGER.error(message)
                    return
                with self.lock:
                    if self.state != "ERROR":
                        self.state = "OPERATIONAL"
                        self.message = (
                            f"Digital I/O running; partial WKC "
                            f"{self.wkc}/{self.expected_wkc}"
                        )
            else:
                with self.lock:
                    if self.state != "ERROR":
                        self.state = "OPERATIONAL"
                        self.message = (
                            "Digital I/O ready"
                            if self.wkc == self.expected_wkc
                            else f"Digital I/O ready; partial WKC {self.wkc}/{self.expected_wkc}"
                        )
            time.sleep(CYCLE_US / 1_000_000)

    def run_welding_loop(self) -> None:
        while self.running:
            with self.lock:
                welding_command_active = self.welding_command_active
                welding_heartbeat = self.welding_heartbeat
            now = time.monotonic()
            if (
                welding_command_active
                and now - welding_heartbeat > HEARTBEAT_TIMEOUT
            ):
                self.stop_welding()
                with self.lock:
                    self.message = "Watchdog stopped the welding command"
                LOGGER.warning(
                    "Welding watchdog stopped command after %.3fs",
                    HEARTBEAT_TIMEOUT,
                )
            self.wkc = self.welding_cycle()
            self.welding_feedback()
            if self.io_profile is not None:
                self.read_io_inputs()
            if not self._process_wkc_is_valid():
                message = (
                    f"Welding process-data WKC mismatch: "
                    f"{self.wkc}/{self.expected_wkc}"
                )
                self._latch_runtime_error(message)
                LOGGER.error(
                    "Welding process-data WKC mismatch: actual=%s expected=%s",
                    self.wkc,
                    self.expected_wkc,
                )
                return
            else:
                with self.lock:
                    if self.state != "ERROR":
                        self.state = (
                            "WELDING"
                            if self.welding_command_active or self.welding_active
                            else "OPERATIONAL"
                        )
                        self.message = (
                            "Welding command active"
                            if self.welding_command_active
                            else "Welding machine ready"
                        )
            time.sleep(CYCLE_US / 1_000_000)

    def request_welding_operational(self) -> None:
        self.master.state = pysoem.SAFEOP_STATE
        self.master.write_state()
        if self.master.state_check(pysoem.SAFEOP_STATE, 200_000) != pysoem.SAFEOP_STATE:
            raise RuntimeError("welding machine did not reach SAFE-OP")
        with self.lock:
            self._clear_welding_command_locked()
            self.io_output_mask = 0
        self.wkc = self.welding_cycle()
        if self.wkc <= 0:
            raise RuntimeError(
                f"welding SAFE-OP process-data exchange failed: {self.wkc}"
            )
        self.master.state = pysoem.OP_STATE
        self.master.write_state()
        if self.master.state_check(pysoem.OP_STATE, 500_000) != pysoem.OP_STATE:
            raise RuntimeError("welding machine did not reach OP")
        LOGGER.info("EtherCAT welding machine reached OP state")

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
            has_drive = self.profile is not None
            has_digital_io = self.io_profile is not None
            has_welding = self.welding_profile is not None
            device_names = [
                device.name
                for device in (self.profile, self.io_profile, self.welding_profile)
                if device is not None
            ]
            device_name = " + ".join(device_names)
            device_type = (
                "mixed"
                if sum((has_drive, has_digital_io, has_welding)) > 1
                else "welding"
                if has_welding
                else "digital_io"
                if has_digital_io
                else "drive"
                if has_drive
                else ""
            )
            connected_states = {
                "OPERATIONAL",
                "ENABLED",
                "JOGGING",
                "PP_MOVING",
                "HOMING",
                "CSP_MOVING",
                "WELDING",
            }
            return {
                "state": self.state,
                "message": self.message,
                "interface": self.interface,
                "device": device_name,
                "deviceType": device_type,
                "devices": device_names,
                "hasDrive": has_drive,
                "hasDigitalIo": has_digital_io,
                "hasWelding": has_welding,
                "driveDevice": self.profile.name if self.profile else "",
                "ioDevice": self.io_profile.name if self.io_profile else "",
                "weldingDevice": self.welding_profile.name if self.welding_profile else "",
                "driveConnected": has_drive and self.state in connected_states,
                "ioConnected": has_digital_io and self.state in connected_states,
                "weldingConnected": has_welding and self.state in connected_states,
                "connected": self.state
                in connected_states,
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
                "cspVelocityLimit": CSP_MAX_VELOCITY,
                "cspAccelerationLimit": CSP_MAX_ACCELERATION,
                "cspFollowingErrorLimit": CSP_MAX_FOLLOWING_ERROR,
                "cspCycleTimeLimit": CSP_MAX_CYCLE_TIME,
                "targetPosition": target_position,
                "actualPosition": self.actual_position,
                "cspFollowingError": self.csp_following_error,
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
                "ioInputMask": self.io_input_mask,
                "ioOutputMask": self.io_output_mask,
                "ioInputMaskHex": (
                    f"0x{self.io_input_mask:0{(self.io_profile.input_channels + 3) // 4}X}"
                    if has_digital_io
                    else "0x0000"
                ),
                "ioOutputMaskHex": (
                    f"0x{self.io_output_mask:0{(self.io_profile.output_channels + 3) // 4}X}"
                    if has_digital_io
                    else "0x0000"
                ),
                "ioInputChannels": self.io_profile.input_channels if has_digital_io else 0,
                "ioOutputChannels": self.io_profile.output_channels if has_digital_io else 0,
                "weldingCommandActive": self.welding_command_active,
                "weldingStart": self.welding_start_welding,
                "weldingRobotReady": self.welding_robot_ready,
                "weldingMode": self.welding_mode,
                "weldingModeValue": welding_mode_value(self.welding_mode),
                "weldingGasTest": self.welding_gas_test,
                "weldingWireInch": self.welding_wire_inch,
                "weldingWireRetract": self.welding_wire_retract,
                "weldingTouchEnable": self.welding_touch_enable,
                "weldingJob": self.welding_job,
                "weldingCurrentOrSpeed": self.welding_current_or_speed,
                "weldingVoltageOrStrength": self.welding_voltage_or_strength,
                "weldingArcSuccess": self.welding_arc_success,
                "weldingActive": self.welding_active,
                "weldingPowerFault": self.welding_power_fault,
                "weldingCommunicationReady": self.welding_communication_ready,
                "weldingFaultCode": self.welding_fault_code,
                "weldingTouchSuccess": self.welding_touch_success,
                "weldingActualVoltage": self.welding_actual_voltage,
                "weldingActualCurrent": self.welding_actual_current,
                "weldingWireSpeed": self.welding_wire_speed,
            }

    def _next_csp_target(self) -> int:
        now = time.monotonic()
        with self.lock:
            if self.csp_move_pending:
                self.csp_move_pending = False
                self.csp_move_active = True
                self.csp_protection_active = True
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

    def _check_csp_protection(self, now: float) -> str | None:
        with self.lock:
            if not self.csp_protection_active or self.csp_command_position is None:
                return None
            if self.csp_last_cycle_at is not None:
                cycle_time = now - self.csp_last_cycle_at
                if cycle_time > CSP_MAX_CYCLE_TIME:
                    return (
                        f"CSP cycle time exceeded: {cycle_time:.4f}s/"
                        f"{CSP_MAX_CYCLE_TIME:.4f}s"
                    )
            self.csp_last_cycle_at = now
            self.csp_following_error = abs(
                self.actual_position - self.csp_command_position
            )
            if self.csp_following_error > CSP_MAX_FOLLOWING_ERROR:
                return (
                    f"CSP following error exceeded: {self.csp_following_error}/"
                    f"{CSP_MAX_FOLLOWING_ERROR}"
                )
            if not self.csp_move_active:
                self.csp_protection_active = False
            return None

    def cycle(self, controlword: int, velocity: int = 0) -> int:
        with self.process_data_lock:
            return self._cycle_unlocked(controlword, velocity)

    def _cycle_unlocked(self, controlword: int, velocity: int = 0) -> int:
        drive_slave = self._drive_device()
        if self.profile is None or drive_slave is None:
            raise RuntimeError("drive process data is not configured")
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
            drive_slave.output = pp_packet(
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
            drive_slave.output = packet(
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
            drive_slave.output = homing_packet(
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
            with self.lock:
                self.csp_command_position = target_position
            mode_in_pdo = mode_pdo.mode_in_pdo if mode_pdo else True
            target_velocity_in_pdo = mode_pdo.target_velocity_in_pdo if mode_pdo else False
            drive_slave.output = csp_packet(
                controlword,
                target_position,
                mode=mode_value,
                mode_in_pdo=mode_in_pdo,
                target_velocity_in_pdo=target_velocity_in_pdo,
            )
        else:
            raise RuntimeError(f"no packet encoder for {mode.upper()} mode")
        self._write_welding_command()
        self._write_io_output()
        self.master.send_overlap_processdata()
        return self.master.receive_processdata(CYCLE_US)

    def feedback(self) -> None:
        drive_slave = self._drive_device()
        if drive_slave is None:
            return
        data = bytes(drive_slave.input)
        if len(data) >= 5:
            self.error = int.from_bytes(data[0:2], "little")
            self.statusword = int.from_bytes(data[2:4], "little")
            self.mode = struct.unpack_from("<b", data, 4)[0]
        if len(data) >= 9:
            self.actual_position = struct.unpack_from("<i", data, 5)[0]

    def enable_drive(self) -> None:
        LOGGER.info("CiA 402 enable sequence started")
        self.enabled = False
        for controlword, expected_state, state_name in CIA402_ENABLE_STEPS:
            for _ in range(CIA402_STATE_RETRIES):
                self.wkc = self.cycle(controlword, 0)
                self.feedback()
                if self.wkc != self.expected_wkc:
                    LOGGER.error("Enable WKC mismatch: actual=%s expected=%s", self.wkc, self.expected_wkc)
                    raise RuntimeError(
                        f"enable process-data WKC mismatch: {self.wkc}"
                    )
                if self.statusword & CIA402_FAULT:
                    raise RuntimeError(
                        f"drive fault during enable, statusword=0x{self.statusword:04X}"
                    )
                if self.statusword & CIA402_STATE_MASK == expected_state:
                    break
                time.sleep(CYCLE_US / 1_000_000)
            else:
                raise RuntimeError(
                    f"CiA 402 enable timeout waiting for {state_name}, "
                    f"statusword=0x{self.statusword:04X}"
                )
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
            return () if self.io_profile is not None or self.welding_profile is not None else MOTION_MODES
        if self.mode_capability_word is None:
            return self.profile.supported_modes
        return modes_from_capability_word(
            self.mode_capability_word,
            self.profile.supported_modes,
        )

    def _read_mode_capabilities(self) -> None:
        drive_slave = self._drive_device()
        if self.profile is None or drive_slave is None:
            raise RuntimeError("drive profile is not available")
        try:
            raw_value = drive_slave.sdo_read(0x6502, 0)
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
        drive_slave = self._drive_device()
        if drive_slave is None or self.profile is None:
            raise RuntimeError("drive profile is not available")
        rx_pdo, rx_bytes, mode_value = self.mode_settings(mode)
        for index, value in ((0x1C12, rx_pdo), (0x1C13, self.profile.tx_pdo)):
            drive_slave.sdo_write(index, 0, b"\x00")
            drive_slave.sdo_write(index, 1, value.to_bytes(2, "little"))
            drive_slave.sdo_write(index, 0, b"\x01")
        drive_slave.sdo_write(0x6040, 0, (0x0080).to_bytes(2, "little"))
        drive_slave.sdo_write(
            0x6060, 0, mode_value.to_bytes(1, "little", signed=True)
        )
        self._validate_drive_pdo_assignments(rx_pdo)
        self.motion_mode = mode
        io_map_size = self._map_process_data(overlap=True)
        actual_sizes = (len(drive_slave.output), len(drive_slave.input))
        expected_sizes = (rx_bytes, self.profile.tx_bytes)
        if actual_sizes != expected_sizes:
            raise RuntimeError(
                f"{self.profile.name} {mode.upper()} expected Rx/Tx bytes "
                f"{expected_sizes[0]}/{expected_sizes[1]}, "
                f"got {actual_sizes[0]}/{actual_sizes[1]}"
            )
        expected_map_size = self._expected_process_image_size(rx_bytes)
        if io_map_size != expected_map_size:
            raise RuntimeError(
                f"unexpected {mode.upper()} process image size: {io_map_size} "
                f"(expected {expected_map_size})"
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

    def request_io_operational(self) -> None:
        self.master.state = pysoem.SAFEOP_STATE
        self.master.write_state()
        if self.master.state_check(pysoem.SAFEOP_STATE, 200_000) != pysoem.SAFEOP_STATE:
            raise RuntimeError("digital I/O device did not reach SAFE-OP")
        with self.lock:
            self.io_output_mask = 0
        self.wkc = self.io_cycle()
        if self.wkc <= 0:
            raise RuntimeError(
                f"digital I/O SAFE-OP process-data exchange failed: {self.wkc}"
            )
        self.master.state = pysoem.OP_STATE
        self.master.write_state()
        if self.master.state_check(pysoem.OP_STATE, 500_000) != pysoem.OP_STATE:
            raise RuntimeError("digital I/O device did not reach OP")
        self.wkc = self.io_cycle()
        LOGGER.info(
            "EtherCAT digital I/O reached OP state (WKC=%d/%d)",
            self.wkc,
            self.expected_wkc,
        )

    def _identify_slaves(self) -> None:
        self.slave = None
        self.drive_slave = None
        self.io_slave = None
        self.welding_slave = None
        self.profile = None
        self.io_profile = None
        self.welding_profile = None
        unsupported: list[str] = []
        for candidate in self.master.slaves:
            revision = getattr(candidate, "rev", None)
            revision = revision if isinstance(revision, int) else None
            drive_profile = get_drive_profile(candidate.man, candidate.id, revision)
            io_profile = get_remote_io_profile(candidate.man, candidate.id, revision)
            welding_profile = get_welding_profile(candidate.man, candidate.id)
            identity = f"0x{candidate.man:08X}/0x{candidate.id:08X}"
            if drive_profile is not None:
                if self.drive_slave is not None:
                    raise RuntimeError("multiple supported EtherCAT drives are not supported")
                self.drive_slave = candidate
                self.profile = drive_profile
            elif io_profile is not None:
                if self.io_slave is not None:
                    raise RuntimeError("multiple supported digital I/O devices are not supported")
                self.io_slave = candidate
                self.io_profile = io_profile
            elif welding_profile is not None:
                if self.welding_slave is not None:
                    raise RuntimeError(
                        "multiple supported Megmeet welding machines are not supported"
                    )
                self.welding_slave = candidate
                self.welding_profile = welding_profile
            else:
                unsupported.append(identity)
        if unsupported:
            raise RuntimeError(
                "unsupported EtherCAT slave(s): " + ", ".join(unsupported)
            )
        if (
            self.drive_slave is None
            and self.io_slave is None
            and self.welding_slave is None
        ):
            raise RuntimeError("no supported EtherCAT device found")
        self.slave = self.drive_slave or self.io_slave or self.welding_slave

    def configure(self) -> None:
        LOGGER.info("Configuring EtherCAT master")
        if self.master.config_init() <= 0:
            raise RuntimeError(
                "no EtherCAT slave detected on the selected interface"
            )
        self._identify_slaves()
        self.master.config_dc()
        if self.profile is not None:
            if self.io_profile is not None:
                self._prepare_io_modules()
            self._validate_welding_process_data()
            self._read_mode_capabilities()
            self.configure_process_data(self.motion_mode)
            if self.io_profile is not None:
                self._validate_io_process_data()
                with self.lock:
                    self.io_input_mask = 0
                    self.io_output_mask = 0
            self.request_operational()
            return
        if self.welding_profile is not None:
            if self.io_profile is not None:
                self._prepare_io_modules()
            self.configure_welding_process_data()
            self.request_welding_operational()
            return
        if self.io_profile is not None:
            self.configure_io_process_data()
            self.request_io_operational()

    def switch_mode(self, mode: str) -> None:
        LOGGER.info("Switching EtherCAT process data to %s mode", mode.upper())
        self._prepare_bus_switch("mode switch")
        self.master.state = pysoem.PREOP_STATE
        self.master.write_state()
        if self.master.state_check(pysoem.PREOP_STATE, 500_000) != pysoem.PREOP_STATE:
            raise RuntimeError("drive did not return to PRE-OP for mode switch")
        if self.master.config_init() <= 0:
            raise RuntimeError("expected at least one EtherCAT slave after mode switch")
        self._identify_slaves()
        if self.io_profile is not None:
            self._prepare_io_modules()
        if self.profile is None:
            raise RuntimeError(
                "unsupported EtherCAT slave after mode switch "
                f"0x{self.slave.man:08X}/0x{self.slave.id:08X}"
            )
        self._read_mode_capabilities()
        self.configure_process_data(mode)
        if self.io_profile is not None:
            self._validate_io_process_data()
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
            self._master_open = True
            LOGGER.info("EtherCAT interface opened")
            self.configure()
            if self.profile is None and self.welding_profile is not None:
                with self.lock:
                    self.state, self.message = "OPERATIONAL", "Welding machine ready"
                self.run_welding_loop()
                return
            if self.profile is None and self.io_profile is not None:
                with self.lock:
                    self.state, self.message = "OPERATIONAL", "Digital I/O ready"
                self.run_io_loop()
                return
            with self.lock:
                self.state, self.message = (
                    "OPERATIONAL",
                    "Ready; drive and digital I/O connected"
                    if self.io_profile is not None
                    else "Ready; hold a Jog button",
                )
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
                    welding_command_active = self.welding_command_active
                    welding_heartbeat = self.welding_heartbeat
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
                if (
                    welding_command_active
                    and now - welding_heartbeat > HEARTBEAT_TIMEOUT
                ):
                    self.stop_welding()
                    LOGGER.warning(
                        "Welding watchdog stopped command after %.3fs",
                        HEARTBEAT_TIMEOUT,
                    )
                    with self.lock:
                        self.message = "Watchdog stopped the welding command"
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
                if self.welding_profile is not None:
                    self.welding_feedback()
                if self.io_profile is not None:
                    self.read_io_inputs()
                if motion_mode == MODE_CSP:
                    csp_error = self._check_csp_protection(now)
                    if csp_error is not None:
                        self._latch_runtime_error(csp_error)
                        LOGGER.error(csp_error)
                        self._transmit_safe_outputs(disable_drive=True)
                        return
                if self.wkc != self.expected_wkc:
                    message = (
                        f"Process-data WKC mismatch: {self.wkc}/"
                        f"{self.expected_wkc}"
                    )
                    self._latch_runtime_error(message)
                    LOGGER.error("Process-data WKC mismatch: actual=%s expected=%s", self.wkc, self.expected_wkc)
                    return
                if self.statusword & CIA402_FAULT:
                    fault_statusword = self.statusword
                    message = (
                        "Drive fault reported by statusword: "
                        f"0x{fault_statusword:04X}"
                    )
                    self._latch_runtime_error(message)
                    LOGGER.error(
                        "Drive fault reported: statusword=0x%04X error=0x%04X",
                        fault_statusword,
                        self.error,
                    )
                    return
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
                    elif self.welding_profile is not None and (
                        self.welding_command_active or self.welding_active
                    ):
                        self.state = "WELDING"
                    else:
                        self.state = "ENABLED" if self.enabled else "OPERATIONAL"
                time.sleep(CYCLE_US / 1_000_000)
        except Exception as exc:
            LOGGER.exception("Runtime loop failed")
            with self.lock:
                self.running = False
                self.state, self.message = "ERROR", f"{type(exc).__name__}: {exc}"
        finally:
            try:
                if self.profile is not None and self._drive_device() is not None:
                    with self.lock:
                        self.io_output_mask = 0
                        self._clear_welding_command_locked()
                    self._transmit_safe_outputs(disable_drive=True)
                elif self.welding_profile is not None and self._welding_device() is not None:
                    with self.lock:
                        self._clear_welding_command_locked()
                        self.io_output_mask = 0
                    self._transmit_safe_outputs(disable_drive=False)
                elif self.io_profile is not None and self._io_device() is not None:
                    with self.lock:
                        self.io_output_mask = 0
                    self._transmit_safe_outputs(disable_drive=False)
            except Exception as exc:
                LOGGER.exception("Failed to transmit final safe outputs")
                detail = f"Final safe output failed: {type(exc).__name__}: {exc}"
                with self.lock:
                    self.running = False
                    self.state = "ERROR"
                    self.message = f"{self.message}; {detail}" if self.message else detail
            try:
                self.master.close()
            except Exception as exc:
                LOGGER.exception("Failed to close EtherCAT master")
                detail = f"Master close failed: {type(exc).__name__}: {exc}"
                with self.lock:
                    self.running = False
                    self.state = "ERROR"
                    self.message = f"{self.message}; {detail}" if self.message else detail
            finally:
                self._master_open = False
            LOGGER.info("EtherCAT interface closed")
