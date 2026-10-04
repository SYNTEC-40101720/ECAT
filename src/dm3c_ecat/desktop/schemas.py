"""WebSocket 命令的严格校验原语（契约层）。

与旧 ``websocket_hmi.py`` 的语义保持一致：
- 消息必须是 JSON object；
- 字段集合精确匹配（缺失、多余都拒绝）；
- bool 严格（``"false"``、0/1 都不合法）、整数严格（bool 不算整数）、
  浮点必须有限（NaN/Infinity 拒绝）、字符串必须是 str。
"""

from __future__ import annotations

import math
from typing import Any

CommandObject = dict[str, Any]


def require_command_object(command: object) -> CommandObject:
    if not isinstance(command, dict):
        raise ValueError("command must be a JSON object")
    return command


def validate_fields(
    command: CommandObject,
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


def require_string(command: CommandObject, field: str) -> str:
    value = command[field]
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def require_bool(command: CommandObject, field: str) -> bool:
    value = command[field]
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be boolean")
    return value


def require_int(command: CommandObject, field: str) -> int:
    value = command[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def require_float(command: CommandObject, field: str) -> float:
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