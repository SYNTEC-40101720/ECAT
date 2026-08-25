from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
from collections.abc import Iterable

from websockets.asyncio.server import ServerConnection, serve

from .device_profiles import enumerate_adapters, resolve_default_interface
from .hmi import LOG_BUFFER, Runtime, _LogCapture
from .logging_setup import DEFAULT_LOG_FILE, configure_logging

LOGGER = logging.getLogger("ecat_test.websocket")


def _require_command_object(command: object) -> dict[str, object]:
    if not isinstance(command, dict):
        raise ValueError("command must be a JSON object")
    return command


def _validate_fields(
    command: dict[str, object],
    required: tuple[str, ...] = (),
    optional: tuple[str, ...] = (),
) -> None:
    missing = [field for field in required if field not in command]
    if missing:
        raise ValueError(f"missing command field: {missing[0]}")
    allowed = {"command", *required, *optional}
    unexpected = sorted(set(command) - allowed)
    if unexpected:
        raise ValueError(f"unexpected command field: {unexpected[0]}")


def _require_string(command: dict[str, object], field: str) -> str:
    value = command[field]
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _require_bool(command: dict[str, object], field: str) -> bool:
    value = command[field]
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be boolean")
    return value


def _require_int(command: dict[str, object], field: str) -> int:
    value = command[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _require_float(command: dict[str, object], field: str) -> float:
    value = command[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    try:
        converted = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(converted):
        raise ValueError(f"{field} must be finite")
    return converted


class WebSocketHmi:
    def __init__(self, runtime: Runtime, shutdown_token: str | None = None) -> None:
        self.runtime = runtime
        self.clients: set[ServerConnection] = set()
        self.shutdown_token = shutdown_token
        self.shutdown_event = asyncio.Event()

    @staticmethod
    def adapter_payload() -> list[dict[str, object]]:
        return [
            {"name": name, "description": description, "selectable": selectable}
            for name, description, selectable in enumerate_adapters()
        ]

    async def send(self, connection: ServerConnection, payload: dict[str, object]) -> None:
        await connection.send(json.dumps(payload, ensure_ascii=False))

    async def broadcast(self, payload: dict[str, object]) -> None:
        if not self.clients:
            return
        message = json.dumps(payload, ensure_ascii=False)
        results = await asyncio.gather(
            *(client.send(message) for client in tuple(self.clients)),
            return_exceptions=True,
        )
        for client, result in zip(tuple(self.clients), results, strict=False):
            if isinstance(result, Exception):
                self.clients.discard(client)

    async def handle_command(self, command: object) -> dict[str, object]:
        command = _require_command_object(command)
        name = _require_string(command, "command")
        if name == "list_adapters":
            _validate_fields(command)
            return {"type": "adapters", "data": self.adapter_payload()}
        if name == "shutdown":
            _validate_fields(command, required=("token",))
            token = _require_string(command, "token")
            if self.shutdown_token is None or token != self.shutdown_token:
                raise ValueError("shutdown is not authorized")
            self.shutdown_event.set()
            return {"type": "ack", "command": name, "accepted": True}
        if name == "select_interface":
            _validate_fields(command, required=("interface",))
            selected = _require_string(command, "interface")
            adapters = {
                adapter_name: selectable
                for adapter_name, _description, selectable in enumerate_adapters()
            }
            if not adapters.get(selected, False):
                raise ValueError("select a listed physical EtherCAT adapter")
            self.runtime.select_interface(selected)
            return {"type": "ack", "command": name, "accepted": True}
        if name == "jog":
            _validate_fields(command, required=("velocity",))
            self.runtime.jog(_require_int(command, "velocity"))
        elif name == "set_mode":
            _validate_fields(command, required=("mode",))
            self.runtime.set_mode(_require_string(command, "mode"))
        elif name == "move_pp":
            _validate_fields(
                command,
                required=(
                    "targetPosition",
                    "velocity",
                    "accelerationTime",
                    "decelerationTime",
                ),
                optional=("relative",),
            )
            self.runtime.move_pp(
                _require_int(command, "targetPosition"),
                _require_int(command, "velocity"),
                _require_float(command, "accelerationTime"),
                _require_float(command, "decelerationTime"),
                _require_bool(command, "relative")
                if "relative" in command
                else False,
            )
        elif name == "start_homing":
            _validate_fields(
                command,
                required=("method", "fastVelocity", "slowVelocity", "accelerationTime"),
                optional=("offset",),
            )
            self.runtime.start_homing(
                _require_int(command, "method"),
                _require_int(command, "fastVelocity"),
                _require_int(command, "slowVelocity"),
                _require_float(command, "accelerationTime"),
                _require_int(command, "offset") if "offset" in command else 0,
            )
        elif name == "homing_keepalive":
            _validate_fields(command)
            self.runtime.homing_keepalive()
        elif name == "move_csp":
            _validate_fields(command, required=("targetPosition", "duration"))
            self.runtime.move_csp(
                _require_int(command, "targetPosition"),
                _require_float(command, "duration"),
            )
        elif name == "csp_keepalive":
            _validate_fields(command)
            self.runtime.csp_keepalive()
        elif name == "pp_keepalive":
            _validate_fields(command)
            self.runtime.pp_keepalive()
        elif name == "stop":
            _validate_fields(command)
            self.runtime.stop_motion()
        elif name == "enable":
            _validate_fields(command)
            self.runtime.enable()
        elif name == "disable":
            _validate_fields(command)
            self.runtime.disable()
        elif name == "set_ramp":
            _validate_fields(command, required=("acceleration", "deceleration"))
            self.runtime.set_ramp_times(
                _require_float(command, "acceleration"),
                _require_float(command, "deceleration"),
            )
        elif name == "set_output":
            _validate_fields(command, required=("channel", "enabled"))
            self.runtime.set_digital_output(
                _require_int(command, "channel"),
                _require_bool(command, "enabled"),
            )
        elif name == "set_welding_command":
            _validate_fields(
                command,
                required=(
                    "startWelding",
                    "robotReady",
                    "mode",
                    "gasTest",
                    "wireInch",
                    "wireRetract",
                    "touchEnable",
                    "job",
                    "currentOrSpeed",
                    "voltageOrStrength",
                ),
            )
            self.runtime.set_welding_command(
                start_welding=_require_bool(command, "startWelding"),
                robot_ready=_require_bool(command, "robotReady"),
                mode=_require_string(command, "mode"),
                gas_test=_require_bool(command, "gasTest"),
                wire_inch=_require_bool(command, "wireInch"),
                wire_retract=_require_bool(command, "wireRetract"),
                touch_enable=_require_bool(command, "touchEnable"),
                job=_require_int(command, "job"),
                current_or_speed=_require_int(command, "currentOrSpeed"),
                voltage_or_strength=_require_int(command, "voltageOrStrength"),
            )
        elif name == "start_welding":
            _validate_fields(command)
            self.runtime.start_welding()
        elif name == "stop_welding":
            _validate_fields(command)
            self.runtime.stop_welding()
        elif name == "welding_keepalive":
            _validate_fields(command)
            self.runtime.welding_keepalive()
        else:
            raise ValueError(f"unknown command: {name}")
        return {"type": "ack", "command": name, "accepted": True}

    async def client(self, connection: ServerConnection) -> None:
        self.clients.add(connection)
        LOGGER.info("WebSocket client connected: %s", connection.remote_address)
        try:
            await self.send(connection, {"type": "adapters", "data": self.adapter_payload()})
            await self.send(connection, {"type": "state", "data": self.runtime.snapshot()})
            async for raw_message in connection:
                try:
                    command = json.loads(raw_message)
                    response = await self.handle_command(command)
                except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                    response = {"type": "error", "error": str(exc)}
                await self.send(connection, response)
        finally:
            self.clients.discard(connection)
            self.runtime.stop()
            LOGGER.info("WebSocket client disconnected")

    async def publish(self, logs: Iterable[str]) -> None:
        for line in logs:
            await self.broadcast({"type": "log", "data": line})

    async def run(self, host: str, port: int) -> None:
        async with serve(self.client, host, port):
            LOGGER.info("WebSocket HMI listening on ws://%s:%s", host, port)
            self.runtime.start()
            last_log_count = len(LOG_BUFFER)
            while not self.shutdown_event.is_set():
                await self.broadcast({"type": "state", "data": self.runtime.snapshot()})
                current_logs = list(LOG_BUFFER)
                if len(current_logs) < last_log_count:
                    last_log_count = 0
                for line in current_logs[last_log_count:]:
                    await self.broadcast({"type": "log", "data": line})
                last_log_count = len(current_logs)
                await asyncio.sleep(0.2)


def main() -> int:
    parser = argparse.ArgumentParser(description="ECAT Test Electron WebSocket backend")
    parser.add_argument("--interface")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_FILE))
    parser.add_argument("--shutdown-token")
    args = parser.parse_args()
    configure_logging(args.log_file)
    capture = _LogCapture()
    capture.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.getLogger("ecat_test").addHandler(capture)
    interface = args.interface or resolve_default_interface()
    runtime = Runtime(interface)
    gateway = WebSocketHmi(runtime, args.shutdown_token)
    try:
        asyncio.run(gateway.run(args.host, args.port))
    except KeyboardInterrupt:
        return 130
    finally:
        runtime.stop()
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())