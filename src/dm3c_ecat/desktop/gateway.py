"""Runtime 命令分发：把校验后的 WS 命令映射为 Runtime 方法调用。

纯逻辑、可单测：不 import FastAPI/WS，只依赖 Runtime 的公开方法。
``ControlGateway.handle_command`` 是唯一入口，行为与旧 ``websocket_hmi``
的 ``WebSocketHmi.handle_command`` 等价（含控制权互斥与 shutdown 令牌），
但绑定/传输层由 FastAPI 提供。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from ..device_profiles import enumerate_adapters
from .schemas import (
    require_bool,
    require_command_object,
    require_float,
    require_int,
    require_string,
    validate_fields,
)

LOGGER = logging.getLogger("ecat_test.desktop.gateway")

CONTROL_COMMANDS = frozenset(
    {
        "select_interface",
        "jog",
        "set_mode",
        "move_pp",
        "start_homing",
        "homing_keepalive",
        "move_csp",
        "csp_keepalive",
        "pp_keepalive",
        "stop",
        "enable",
        "disable",
        "set_ramp",
        "set_output",
        "set_welding_command",
        "start_welding",
        "stop_welding",
        "welding_keepalive",
    }
)


class RuntimeProtocol(Protocol):
    """ControlGateway 依赖的 Runtime 表面（hmi.Runtime 完整满足）。"""

    def start(self) -> None: ...
    def snapshot(self) -> dict[str, object]: ...
    def select_interface(self, interface: str) -> None: ...
    def jog(self, velocity: int) -> None: ...
    def set_mode(self, mode: str) -> None: ...
    def move_pp(
        self,
        target_position: int,
        velocity: int,
        acceleration_time: float,
        deceleration_time: float,
        relative: bool = False,
    ) -> None: ...
    def start_homing(
        self,
        method: int,
        fast_velocity: int,
        slow_velocity: int,
        acceleration_time: float,
        offset: int = 0,
    ) -> None: ...
    def homing_keepalive(self) -> None: ...
    def move_csp(self, target_position: int, duration: float) -> None: ...
    def csp_keepalive(self) -> None: ...
    def pp_keepalive(self) -> None: ...
    def stop_motion(self) -> None: ...
    def stop(self) -> None: ...
    def enable(self) -> None: ...
    def disable(self) -> None: ...
    def set_ramp_times(self, acceleration_time: float, deceleration_time: float) -> None: ...
    def set_digital_output(self, channel: int, enabled: bool) -> None: ...
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
    ) -> None: ...
    def start_welding(self) -> None: ...
    def stop_welding(self) -> None: ...
    def welding_keepalive(self) -> None: ...


class ClientConnection(Protocol):
    """WS 连接的身份句柄（FastAPI WebSocket 满足同一性比较）。"""

    def __hash__(self) -> int: ...


def adapter_payload() -> list[dict[str, object]]:
    return [
        {"name": name, "description": description, "selectable": selectable}
        for name, description, selectable in enumerate_adapters()
    ]


def control_payload(
    owner: ClientConnection | None, connection: ClientConnection | None = None
) -> dict[str, object]:
    return {
        "type": "control",
        "available": owner is None,
        "owned": connection is not None and owner is connection,
    }


class ControlGateway:
    """单控制客户端所有权 + 命令校验 + Runtime 分发。

    线程模型：FastAPI 事件循环内单线程访问；``asyncio.Event`` 作为
    shutdown 信号，由 launcher 监听。
    """

    def __init__(
        self,
        runtime: RuntimeProtocol,
        shutdown_token: str | None = None,
        adapters_provider: Any = None,
        local_token: str | None = None,
    ) -> None:
        self.runtime = runtime
        self.shutdown_token = shutdown_token
        # WS 连接令牌：None 表示不启用连接鉴权（单测）；launcher 总是设置。
        self.local_token = local_token
        self.control_owner: ClientConnection | None = None
        self.shutdown_event = asyncio.Event()
        self._adapters_provider = adapters_provider or adapter_payload

    def ws_authorized(self, provided: str | None) -> bool:
        """校验 WS 连接携带的启动令牌（launcher 注入窗口 URL 的 ?token=）。"""
        if self.local_token is None:
            return True
        return provided == self.local_token

    def adapter_payload(self) -> list[dict[str, object]]:
        return list(self._adapters_provider())

    def release_owner(self, connection: ClientConnection) -> bool:
        """连接断开时清理所有权；返回是否需要停止 Runtime。"""
        if self.control_owner is not connection:
            return False
        self.control_owner = None
        return True

    async def handle_command(
        self, command: object, connection: ClientConnection | None = None
    ) -> dict[str, object]:
        command = require_command_object(command)
        name = require_string(command, "command")
        if name == "acquire_control":
            validate_fields(command)
            if connection is None:
                raise ValueError("control acquisition requires a client connection")
            if self.control_owner not in (None, connection):
                return {
                    "type": "control",
                    "command": name,
                    "accepted": False,
                    "owned": False,
                    "reason": "control is already held",
                }
            self.control_owner = connection
            response = control_payload(self.control_owner, connection)
            response.update({"command": name, "accepted": True})
            return response
        if name == "release_control":
            validate_fields(command)
            if connection is None or self.control_owner is not connection:
                raise ValueError("control ownership required")
            # stop_motion 含一次 10ms EtherCAT 收发（_transmit_safe_outputs），
            # 不能在事件循环里同步等它。
            await asyncio.to_thread(self.runtime.stop_motion)
            self.control_owner = None
            return {"type": "control", "command": name, "accepted": True}
        if connection is not None and name in CONTROL_COMMANDS:
            if self.control_owner is not connection:
                raise ValueError("control ownership required")
        if name == "list_adapters":
            validate_fields(command)
            return {"type": "adapters", "data": self.adapter_payload()}
        if name == "shutdown":
            validate_fields(command, required=("token",))
            token = require_string(command, "token")
            if self.shutdown_token is None or token != self.shutdown_token:
                raise ValueError("shutdown is not authorized")
            self.shutdown_event.set()
            return {"type": "ack", "command": name, "accepted": True}
        if name == "select_interface":
            validate_fields(command, required=("interface",))
            selected = require_string(command, "interface")
            selectable = {
                adapter["name"]: adapter["selectable"]
                for adapter in self.adapter_payload()
            }
            if not selectable.get(selected, False):
                raise ValueError("select a listed physical EtherCAT adapter")
            # select_interface 会 join 旧 runtime 线程（最长 2s）并重配置总
            # 线，直接调用会把 200ms 快照广播和所有 WS 命令卡住同样久。
            await asyncio.to_thread(self.runtime.select_interface, selected)
        elif name == "jog":
            validate_fields(command, required=("velocity",))
            self.runtime.jog(require_int(command, "velocity"))
        elif name == "set_mode":
            validate_fields(command, required=("mode",))
            self.runtime.set_mode(require_string(command, "mode"))
        elif name == "move_pp":
            validate_fields(
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
                require_int(command, "targetPosition"),
                require_int(command, "velocity"),
                require_float(command, "accelerationTime"),
                require_float(command, "decelerationTime"),
                require_bool(command, "relative")
                if "relative" in command
                else False,
            )
        elif name == "start_homing":
            validate_fields(
                command,
                required=("method", "fastVelocity", "slowVelocity", "accelerationTime"),
                optional=("offset",),
            )
            self.runtime.start_homing(
                require_int(command, "method"),
                require_int(command, "fastVelocity"),
                require_int(command, "slowVelocity"),
                require_float(command, "accelerationTime"),
                require_int(command, "offset") if "offset" in command else 0,
            )
        elif name == "homing_keepalive":
            validate_fields(command)
            self.runtime.homing_keepalive()
        elif name == "move_csp":
            validate_fields(command, required=("targetPosition", "duration"))
            self.runtime.move_csp(
                require_int(command, "targetPosition"),
                require_float(command, "duration"),
            )
        elif name == "csp_keepalive":
            validate_fields(command)
            self.runtime.csp_keepalive()
        elif name == "pp_keepalive":
            validate_fields(command)
            self.runtime.pp_keepalive()
        elif name == "stop":
            validate_fields(command)
            # stop_motion 的安全帧同样是一次 EtherCAT 收发，交给线程池。
            await asyncio.to_thread(self.runtime.stop_motion)
        elif name == "enable":
            validate_fields(command)
            self.runtime.enable()
        elif name == "disable":
            validate_fields(command)
            # disable 含禁用安全帧（_transmit_safe_outputs），不能阻塞事件
            # 循环。
            await asyncio.to_thread(self.runtime.disable)
        elif name == "set_ramp":
            validate_fields(command, required=("acceleration", "deceleration"))
            self.runtime.set_ramp_times(
                require_float(command, "acceleration"),
                require_float(command, "deceleration"),
            )
        elif name == "set_output":
            validate_fields(command, required=("channel", "enabled"))
            self.runtime.set_digital_output(
                require_int(command, "channel"),
                require_bool(command, "enabled"),
            )
        elif name == "set_welding_command":
            validate_fields(
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
                start_welding=require_bool(command, "startWelding"),
                robot_ready=require_bool(command, "robotReady"),
                mode=require_string(command, "mode"),
                gas_test=require_bool(command, "gasTest"),
                wire_inch=require_bool(command, "wireInch"),
                wire_retract=require_bool(command, "wireRetract"),
                touch_enable=require_bool(command, "touchEnable"),
                job=require_int(command, "job"),
                current_or_speed=require_int(command, "currentOrSpeed"),
                voltage_or_strength=require_int(command, "voltageOrStrength"),
            )
        elif name == "start_welding":
            validate_fields(command)
            self.runtime.start_welding()
        elif name == "stop_welding":
            validate_fields(command)
            self.runtime.stop_welding()
        elif name == "welding_keepalive":
            validate_fields(command)
            self.runtime.welding_keepalive()
        else:
            raise ValueError(f"unknown command: {name}")
        return {"type": "ack", "command": name, "accepted": True}