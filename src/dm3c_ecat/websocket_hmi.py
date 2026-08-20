from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections.abc import Iterable

from websockets.asyncio.server import ServerConnection, serve

from .device_profiles import enumerate_adapters, resolve_default_interface
from .hmi import LOG_BUFFER, Runtime, _LogCapture
from .logging_setup import DEFAULT_LOG_FILE, configure_logging

LOGGER = logging.getLogger("ecat_test.websocket")


class WebSocketHmi:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.clients: set[ServerConnection] = set()

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

    async def handle_command(self, command: dict[str, object]) -> dict[str, object]:
        name = command.get("command")
        if name == "list_adapters":
            return {"type": "adapters", "data": self.adapter_payload()}
        if name == "select_interface":
            selected = str(command["interface"])
            adapters = {
                adapter_name: selectable
                for adapter_name, _description, selectable in enumerate_adapters()
            }
            if not adapters.get(selected, False):
                raise ValueError("select a listed physical EtherCAT adapter")
            self.runtime.select_interface(selected)
            return {"type": "ack", "command": name, "accepted": True}
        if name == "jog":
            self.runtime.jog(int(command["velocity"]))
        elif name == "set_mode":
            self.runtime.set_mode(str(command["mode"]))
        elif name == "move_pp":
            self.runtime.move_pp(
                int(command["targetPosition"]),
                int(command["velocity"]),
                float(command["accelerationTime"]),
                float(command["decelerationTime"]),
                bool(command.get("relative", False)),
            )
        elif name == "start_homing":
            self.runtime.start_homing(
                int(command["method"]),
                int(command["fastVelocity"]),
                int(command["slowVelocity"]),
                float(command["accelerationTime"]),
                int(command.get("offset", 0)),
            )
        elif name == "homing_keepalive":
            self.runtime.homing_keepalive()
        elif name == "move_csp":
            self.runtime.move_csp(
                int(command["targetPosition"]),
                float(command["duration"]),
            )
        elif name == "csp_keepalive":
            self.runtime.csp_keepalive()
        elif name == "pp_keepalive":
            self.runtime.pp_keepalive()
        elif name == "stop":
            self.runtime.stop_motion()
        elif name == "enable":
            self.runtime.enable()
        elif name == "disable":
            self.runtime.disable()
        elif name == "set_ramp":
            self.runtime.set_ramp_times(
                float(command["acceleration"]), float(command["deceleration"])
            )
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
            last_log_count = len(LOG_BUFFER)
            while True:
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
    args = parser.parse_args()
    configure_logging(args.log_file)
    capture = _LogCapture()
    capture.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.getLogger("ecat_test").addHandler(capture)
    interface = args.interface or resolve_default_interface()
    runtime = Runtime(interface)
    runtime.start()
    gateway = WebSocketHmi(runtime)
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