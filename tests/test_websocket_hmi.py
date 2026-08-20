from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

import dm3c_ecat.websocket_hmi as websocket_hmi


def test_select_interface_accepts_only_physical_adapter(monkeypatch):
    monkeypatch.setattr(
        websocket_hmi,
        "enumerate_adapters",
        lambda: [
            ("physical", "Realtek Ethernet", True),
            ("virtual", "WAN Miniport", False),
        ],
    )
    runtime = MagicMock()
    gateway = websocket_hmi.WebSocketHmi(runtime)

    response = asyncio.run(
        gateway.handle_command({"command": "select_interface", "interface": "physical"})
    )

    assert response == {"type": "ack", "command": "select_interface", "accepted": True}
    runtime.select_interface.assert_called_once_with("physical")


def test_select_interface_rejects_virtual_adapter(monkeypatch):
    monkeypatch.setattr(
        websocket_hmi,
        "enumerate_adapters",
        lambda: [("virtual", "WAN Miniport", False)],
    )
    gateway = websocket_hmi.WebSocketHmi(MagicMock())

    with pytest.raises(ValueError, match="physical"):
        asyncio.run(
            gateway.handle_command(
                {"command": "select_interface", "interface": "virtual"}
            )
        )


def test_move_pp_passes_ramp_times_to_runtime():
    runtime = MagicMock()
    gateway = websocket_hmi.WebSocketHmi(runtime)

    response = asyncio.run(
        gateway.handle_command(
            {
                "command": "move_pp",
                "targetPosition": 1000,
                "velocity": 2000,
                "accelerationTime": 0.5,
                "decelerationTime": 1.0,
                "relative": False,
            }
        )
    )

    assert response == {"type": "ack", "command": "move_pp", "accepted": True}
    runtime.move_pp.assert_called_once_with(1000, 2000, 0.5, 1.0, False)


def test_homing_commands_pass_parameters_to_runtime():
    runtime = MagicMock()
    gateway = websocket_hmi.WebSocketHmi(runtime)

    response = asyncio.run(
        gateway.handle_command(
            {
                "command": "start_homing",
                "method": 35,
                "fastVelocity": 500,
                "slowVelocity": 100,
                "accelerationTime": 0.5,
                "offset": -10,
            }
        )
    )

    assert response == {"type": "ack", "command": "start_homing", "accepted": True}
    runtime.start_homing.assert_called_once_with(35, 500, 100, 0.5, -10)


def test_csp_commands_pass_parameters_to_runtime():
    runtime = MagicMock()
    gateway = websocket_hmi.WebSocketHmi(runtime)

    response = asyncio.run(
        gateway.handle_command(
            {"command": "move_csp", "targetPosition": 1200, "duration": 1.0}
        )
    )

    assert response == {"type": "ack", "command": "move_csp", "accepted": True}
    runtime.move_csp.assert_called_once_with(1200, 1.0)


def test_digital_output_command_passes_channel_and_state():
    runtime = MagicMock()
    gateway = websocket_hmi.WebSocketHmi(runtime)

    response = asyncio.run(
        gateway.handle_command(
            {"command": "set_output", "channel": 4, "enabled": True}
        )
    )

    assert response == {"type": "ack", "command": "set_output", "accepted": True}
    runtime.set_digital_output.assert_called_once_with(4, True)