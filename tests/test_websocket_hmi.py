from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

import dm3c_ecat.websocket_hmi as websocket_hmi


def test_run_listens_before_starting_runtime(monkeypatch):
    events = []

    class StopRun(Exception):
        pass

    class FakeServer:
        async def __aenter__(self):
            events.append("listening")
            return self

        async def __aexit__(self, *_args):
            events.append("closed")

    def fake_serve(handler, host, port):
        del handler, host, port
        return FakeServer()

    async def stop_after_first_tick(_delay):
        raise StopRun

    monkeypatch.setattr(websocket_hmi, "serve", fake_serve)
    monkeypatch.setattr(websocket_hmi.asyncio, "sleep", stop_after_first_tick)
    runtime = MagicMock()
    runtime.start.side_effect = lambda: events.append("runtime-start")
    runtime.snapshot.return_value = {}

    with pytest.raises(StopRun):
        asyncio.run(websocket_hmi.WebSocketHmi(runtime).run("127.0.0.1", 8765))

    assert events[:2] == ["listening", "runtime-start"]


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


def test_command_must_be_a_json_object():
    gateway = websocket_hmi.WebSocketHmi(MagicMock())

    with pytest.raises(ValueError, match="JSON object"):
        asyncio.run(gateway.handle_command([]))


def test_command_rejects_string_boolean_values():
    runtime = MagicMock()
    gateway = websocket_hmi.WebSocketHmi(runtime)

    with pytest.raises(ValueError, match="startWelding must be boolean"):
        asyncio.run(
            gateway.handle_command(
                {
                    "command": "set_welding_command",
                    "startWelding": "false",
                    "robotReady": False,
                    "mode": "job",
                    "gasTest": False,
                    "wireInch": False,
                    "wireRetract": False,
                    "touchEnable": False,
                    "job": 0,
                    "currentOrSpeed": 0,
                    "voltageOrStrength": 0,
                }
            )
        )

    runtime.set_welding_command.assert_not_called()


def test_command_rejects_non_finite_numbers_and_unexpected_fields():
    gateway = websocket_hmi.WebSocketHmi(MagicMock())

    with pytest.raises(ValueError, match="duration must be finite"):
        asyncio.run(
            gateway.handle_command(
                {"command": "move_csp", "targetPosition": 100, "duration": float("nan")}
            )
        )

    with pytest.raises(ValueError, match="duration must be finite"):
        asyncio.run(
            gateway.handle_command(
                {"command": "move_csp", "targetPosition": 100, "duration": 10**400}
            )
        )

    with pytest.raises(ValueError, match="unexpected command field"):
        asyncio.run(
            gateway.handle_command(
                {"command": "enable", "unexpected": True}
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


def test_welding_commands_pass_all_parameters_and_controls():
    runtime = MagicMock()
    gateway = websocket_hmi.WebSocketHmi(runtime)
    command = {
        "command": "set_welding_command",
        "startWelding": True,
        "robotReady": True,
        "mode": "pulse_unified",
        "gasTest": True,
        "wireInch": False,
        "wireRetract": True,
        "touchEnable": True,
        "job": 7,
        "currentOrSpeed": 420,
        "voltageOrStrength": 320,
    }

    response = asyncio.run(gateway.handle_command(command))

    assert response == {
        "type": "ack",
        "command": "set_welding_command",
        "accepted": True,
    }
    runtime.set_welding_command.assert_called_once_with(
        start_welding=True,
        robot_ready=True,
        mode="pulse_unified",
        gas_test=True,
        wire_inch=False,
        wire_retract=True,
        touch_enable=True,
        job=7,
        current_or_speed=420,
        voltage_or_strength=320,
    )

    for name, method in (
        ("start_welding", runtime.start_welding),
        ("stop_welding", runtime.stop_welding),
        ("welding_keepalive", runtime.welding_keepalive),
    ):
        response = asyncio.run(gateway.handle_command({"command": name}))
        assert response == {"type": "ack", "command": name, "accepted": True}
        method.assert_called_once_with()


def test_shutdown_requires_matching_token():
    gateway = websocket_hmi.WebSocketHmi(MagicMock(), shutdown_token="secret")

    with pytest.raises(ValueError, match="not authorized"):
        asyncio.run(
            gateway.handle_command({"command": "shutdown", "token": "wrong"})
        )

    response = asyncio.run(
        gateway.handle_command({"command": "shutdown", "token": "secret"})
    )

    assert response == {"type": "ack", "command": "shutdown", "accepted": True}
    assert gateway.shutdown_event.is_set()