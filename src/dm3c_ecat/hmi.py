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

from .device_profiles import DEFAULT_INTERFACE, get_drive_profile
from .logging_setup import DEFAULT_LOG_FILE, configure_logging

LOGGER = logging.getLogger("dm3c_ecat.runtime")

CYCLE_US = 10_000
HEARTBEAT_TIMEOUT = 0.35
MAX_VELOCITY = 100_000
DEFAULT_RAMP_TIME = 0.5
MIN_RAMP_TIME = 0.01
MAX_RAMP_TIME = 60.0
MAX_ACCELERATION = 10_000_000
MODE_PV = "pv"
MODE_PP = "pp"
PP_NEW_SETPOINT = 0x0010
PP_RELATIVE = 0x0040
PP_HALT = 0x0100
PP_TARGET_REACHED = 0x0400
MIN_POSITION = -(1 << 31)
MAX_POSITION = (1 << 31) - 1

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


class Runtime:
    def __init__(self, interface: str) -> None:
        self.interface = interface
        self.master = pysoem.Master()
        self.slave = None
        self.profile = None
        self.lock = threading.Lock()
        self.running = True
        self.command = 0
        self.heartbeat = 0.0
        self.state = "STARTING"
        self.message = ""
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
        self.actual_position = 0
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def start(self) -> None:
        LOGGER.info("Runtime start requested: interface=%s", self.interface)
        self.thread.start()

    def close(self) -> None:
        LOGGER.info("Runtime close requested")
        self.running = False
        self.thread.join(timeout=2)

    def set_ramp_times(self, acceleration_time: float, deceleration_time: float) -> None:
        if not MIN_RAMP_TIME <= acceleration_time <= MAX_RAMP_TIME:
            raise ValueError(f"acceleration time must be {MIN_RAMP_TIME}-{MAX_RAMP_TIME}s")
        if not MIN_RAMP_TIME <= deceleration_time <= MAX_RAMP_TIME:
            raise ValueError(f"deceleration time must be {MIN_RAMP_TIME}-{MAX_RAMP_TIME}s")
        with self.lock:
            self.acceleration_time = acceleration_time
            self.deceleration_time = deceleration_time
            LOGGER.info(
                "Ramp times set: acceleration=%.3fs deceleration=%.3fs",
                acceleration_time,
                deceleration_time,
            )

    def ramp_values(self, velocity: int) -> tuple[int, int]:
        with self.lock:
            acceleration_time = self.acceleration_time
            deceleration_time = self.deceleration_time
            last_target_velocity = self.last_target_velocity
        magnitude = max(1, abs(velocity or last_target_velocity))
        acceleration = min(MAX_ACCELERATION, max(1, round(magnitude / acceleration_time)))
        deceleration = min(MAX_ACCELERATION, max(1, round(magnitude / deceleration_time)))
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
            if self.motion_mode != MODE_PV:
                raise RuntimeError("Jog is only available in PV mode")
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
        if mode not in {MODE_PV, MODE_PP}:
            raise ValueError(f"unsupported motion mode: {mode}")
        with self.lock:
            if self.state == "ERROR":
                raise RuntimeError("drive runtime is in ERROR state")
            if mode == self.motion_mode and self.pending_mode is None:
                return
            if (
                self.enable_requested
                or self.enabled
                or self.command
                or self.pp_move_pending
                or self.pp_move_active
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
        acceleration: int,
        deceleration: int,
        relative: bool = False,
    ) -> None:
        if not MIN_POSITION <= target_position <= MAX_POSITION:
            raise ValueError(f"target position must be {MIN_POSITION}..{MAX_POSITION}")
        if not 1 <= velocity <= MAX_VELOCITY:
            raise ValueError(f"profile velocity must be 1..{MAX_VELOCITY}")
        if not 1 <= acceleration <= MAX_ACCELERATION:
            raise ValueError(f"acceleration must be 1..{MAX_ACCELERATION}")
        if not 1 <= deceleration <= MAX_ACCELERATION:
            raise ValueError(f"deceleration must be 1..{MAX_ACCELERATION}")
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
                "PP move requested: target=%s velocity=%s acceleration=%s "
                "deceleration=%s relative=%s",
                target_position,
                velocity,
                acceleration,
                deceleration,
                relative,
            )

    def pp_keepalive(self) -> None:
        with self.lock:
            if self.motion_mode == MODE_PP and (
                self.pp_move_pending or self.pp_move_active
            ):
                self.pp_heartbeat = time.monotonic()

    def enable(self) -> None:
        with self.lock:
            if self.state == "ERROR":
                raise RuntimeError("drive runtime is in ERROR state")
            self.enable_requested = True
            self.state = "ENABLING"
            self.message = "Enabling drive..."
            LOGGER.info("Enable requested")

    def stop_motion(self) -> None:
        with self.lock:
            had_motion = bool(
                self.command or self.pp_move_pending or self.pp_move_active
            )
            self.command = 0
            self.heartbeat = 0
            self.pp_move_pending = False
            self.pp_move_active = False
            self.pp_trigger = False
            self.pp_halted = self.motion_mode == MODE_PP
            self.pp_heartbeat = 0
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
            LOGGER.info("Drive disable requested")

    def stop(self) -> None:
        self.disable()

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            return {
                "state": self.state,
                "message": self.message,
                "interface": self.interface,
                "device": self.profile.name if self.profile else "",
                "connected": self.state
                in {"OPERATIONAL", "ENABLED", "JOGGING", "PP_MOVING"},
                "enabled": self.enabled,
                "enableRequested": self.enable_requested,
                "motionMode": self.motion_mode,
                "motionModeValue": 1 if self.motion_mode == MODE_PP else 3,
                "velocityCommand": self.command,
                "velocityLimit": MAX_VELOCITY,
                "targetPosition": self.pp_target_position,
                "actualPosition": self.actual_position,
                "ppMoving": self.pp_move_pending or self.pp_move_active,
                "targetReached": bool(self.statusword & PP_TARGET_REACHED),
                "statusword": f"0x{self.statusword:04X}",
                "errorCode": f"0x{self.error:04X}",
                "mode": self.mode,
                "wkc": self.wkc,
                "expectedWkc": self.expected_wkc,
            }

    def cycle(self, controlword: int, velocity: int = 0) -> int:
        if self.motion_mode == MODE_PP:
            with self.lock:
                target_position = self.pp_target_position
                profile_velocity = self.pp_profile_velocity
                acceleration = self.pp_acceleration
                deceleration = self.pp_deceleration
            mode = self.profile.pp_mode if self.profile else 1
            self.slave.output = pp_packet(
                controlword,
                target_position,
                profile_velocity,
                acceleration,
                deceleration,
                mode,
            )
        else:
            ramp_velocity = velocity or self.last_target_velocity
            acceleration, deceleration = self.ramp_values(ramp_velocity)
            mode = self.profile.mode if self.profile else 3
            self.slave.output = packet(
                controlword, velocity, acceleration, deceleration, mode
            )
        self.master.send_processdata()
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
        if mode == MODE_PV:
            return self.profile.rx_pdo, self.profile.rx_bytes, self.profile.mode
        if mode == MODE_PP:
            return self.profile.pp_rx_pdo, self.profile.pp_rx_bytes, self.profile.pp_mode
        raise ValueError(f"unsupported motion mode: {mode}")

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
        self.configure_process_data(self.motion_mode)
        self.request_operational()

    def switch_mode(self, mode: str) -> None:
        LOGGER.info("Switching EtherCAT process data to %s mode", mode.upper())
        self.master.state = pysoem.PREOP_STATE
        self.master.write_state()
        if self.master.state_check(pysoem.PREOP_STATE, 500_000) != pysoem.PREOP_STATE:
            raise RuntimeError("drive did not return to PRE-OP for mode switch")
        self.configure_process_data(mode)
        self.request_operational()
        with self.lock:
            self.pending_mode = None
            self.enabled = False
            self.pp_move_pending = False
            self.pp_move_active = False
            self.pp_trigger = False
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
                if command and time.monotonic() - heartbeat > HEARTBEAT_TIMEOUT:
                    self.stop_motion()
                    command = 0
                    LOGGER.warning("Heartbeat watchdog stopped motion after %.3fs", HEARTBEAT_TIMEOUT)
                    with self.lock:
                        self.message = "Watchdog stopped the drive"
                if (
                    motion_mode == MODE_PP
                    and (pp_move_pending or pp_move_active)
                    and time.monotonic() - pp_heartbeat > HEARTBEAT_TIMEOUT
                ):
                    self.stop_motion()
                    LOGGER.warning(
                        "PP watchdog stopped motion after %.3fs", HEARTBEAT_TIMEOUT
                    )
                    with self.lock:
                        self.message = "PP watchdog stopped the drive"
                with self.lock:
                    enable_requested = self.enable_requested
                pp_trigger_sent = False
                if enable_requested and not self.enabled:
                    self.enable_drive()
                    with self.lock:
                        self.state = "ENABLED"
                        self.message = (
                            "Drive enabled; hold a Jog button"
                            if motion_mode == MODE_PV
                            else "Drive enabled; submit a PP target"
                        )
                if not enable_requested:
                    self.wkc = self.cycle(0x0006, 0)
                    self.enabled = False
                elif motion_mode == MODE_PV and command:
                    self.wkc = self.cycle(0x000F, command)
                elif motion_mode == MODE_PV:
                    self.wkc = self.cycle(0x000F, 0)
                else:
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
                with self.lock:
                    if self.motion_mode == MODE_PP and self.pp_move_active:
                        self.state = "PP_MOVING"
                    elif self.motion_mode == MODE_PV and command:
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
            return {"adapters": [{"name": n, "desc": d} for n, d in pysoem.find_adapters()]}
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
    parser.add_argument("--interface", default=DEFAULT_INTERFACE)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5090)
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_FILE))
    args = parser.parse_args()
    configure_logging(args.log_file)
    LOGGER.info("Starting browser HMI")
    runtime = Runtime(args.interface)
    Handler.runtime = runtime
    Handler.web_root = WEB_ROOT

    capture = _LogCapture()
    capture.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.getLogger("dm3c_ecat").addHandler(capture)

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
