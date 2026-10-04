"""desktop 包的单元测试（不启动窗口/uvicorn/真实网卡）。"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from dm3c_ecat.desktop.app import create_app
from dm3c_ecat.desktop.gateway import ControlGateway, control_payload


def test_command_must_be_a_json_object():
    gateway = ControlGateway(MagicMock())

    with pytest.raises(ValueError, match="JSON object"):
        asyncio.run(gateway.handle_command([]))


def test_command_rejects_string_boolean_and_unexpected_fields():
    runtime = MagicMock()
    gateway = ControlGateway(runtime)

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

    with pytest.raises(ValueError, match="unexpected command field"):
        asyncio.run(gateway.handle_command({"command": "enable", "extra": 1}))


def test_command_rejects_non_finite_numbers():
    gateway = ControlGateway(MagicMock())

    with pytest.raises(ValueError, match="duration must be finite"):
        asyncio.run(
            gateway.handle_command(
                {"command": "move_csp", "targetPosition": 100, "duration": float("nan")}
            )
        )


def test_motion_commands_pass_parameters_to_runtime():
    runtime = MagicMock()
    gateway = ControlGateway(runtime)

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

    asyncio.run(
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
    runtime.start_homing.assert_called_once_with(35, 500, 100, 0.5, -10)

    asyncio.run(
        gateway.handle_command(
            {"command": "move_csp", "targetPosition": 1200, "duration": 1.0}
        )
    )
    runtime.move_csp.assert_called_once_with(1200, 1.0)

    asyncio.run(
        gateway.handle_command({"command": "set_output", "channel": 4, "enabled": True})
    )
    runtime.set_digital_output.assert_called_once_with(4, True)


def test_shutdown_requires_matching_token():
    gateway = ControlGateway(MagicMock(), shutdown_token="secret")

    with pytest.raises(ValueError, match="not authorized"):
        asyncio.run(gateway.handle_command({"command": "shutdown", "token": "wrong"}))

    response = asyncio.run(
        gateway.handle_command({"command": "shutdown", "token": "secret"})
    )
    assert response == {"type": "ack", "command": "shutdown", "accepted": True}
    assert gateway.shutdown_event.is_set()


def test_start_server_wires_shutdown_token_for_headless_exit(monkeypatch, tmp_path):
    """"start_server 必须给网关装配 shutdown_token，否则 --no-window 无法退出。"""
    import uvicorn

    from dm3c_ecat.desktop import launcher

    gateway = ControlGateway(MagicMock())
    monkeypatch.setattr(launcher, "create_app", lambda gw, static_dir=None: MagicMock())
    monkeypatch.setattr(
        uvicorn, "Server", lambda config: MagicMock(run=lambda: None)
    )
    # uvicorn.Server.run 在线程里被调用；让它立即结束并不影响就绪轮询语义
    monkeypatch.setattr(
        launcher.threading, "Thread", lambda target, name, daemon: MagicMock()
    )
    monkeypatch.setattr(launcher, "_acquire_lock", lambda port_file: None)

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(url, timeout=None):
        return FakeResponse()

    monkeypatch.setattr(launcher.urllib.request, "urlopen", fake_urlopen)

    info = launcher.start_server(gateway, None, port=8642, state_dir=tmp_path)

    assert info["port"] == 8642
    # 生产装配路径：连接令牌与关闭令牌同源，浏览器态 shutdown 因此可用
    assert gateway.shutdown_token == gateway.local_token == info["token"]


def test_only_one_client_can_acquire_control_and_observers_cannot_command():
    runtime = MagicMock()
    gateway = ControlGateway(runtime)
    controller, observer = object(), object()

    response = asyncio.run(
        gateway.handle_command({"command": "acquire_control"}, controller)
    )
    assert response["accepted"] is True
    assert response["owned"] is True

    response = asyncio.run(
        gateway.handle_command({"command": "acquire_control"}, observer)
    )
    assert response == {
        "type": "control",
        "command": "acquire_control",
        "accepted": False,
        "reason": "control is already held",
    }
    with pytest.raises(ValueError, match="control ownership required"):
        asyncio.run(gateway.handle_command({"command": "stop"}, observer))
    runtime.stop_motion.assert_not_called()


def test_owner_disconnect_releases_control_and_requires_stop():
    runtime = MagicMock()
    gateway = ControlGateway(runtime)
    controller = object()

    asyncio.run(gateway.handle_command({"command": "acquire_control"}, controller))
    assert gateway.release_owner(object()) is False
    assert gateway.release_owner(controller) is True
    runtime.stop.assert_not_called()
    # release_owner 只做所有权清理；调用方（app 层）负责执行 stop()


def test_select_interface_accepts_only_physical_adapter():
    gateway = ControlGateway(
        MagicMock(),
        adapters_provider=lambda: [
            {"name": "physical", "description": "Realtek Ethernet", "selectable": True},
            {"name": "virtual", "description": "WAN Miniport", "selectable": False},
        ],
    )

    with pytest.raises(ValueError, match="physical"):
        asyncio.run(
            gateway.handle_command(
                {"command": "select_interface", "interface": "virtual"}
            )
        )
    response = asyncio.run(
        gateway.handle_command(
            {"command": "select_interface", "interface": "physical"}
        )
    )
    assert response["accepted"] is True


def test_control_payload_reflects_ownership():
    owner = object()
    assert control_payload(None) == {"type": "control", "available": True, "owned": False}
    assert control_payload(owner, owner) == {
        "type": "control",
        "available": False,
        "owned": True,
    }


def test_create_app_requires_built_static_dir(tmp_path):
    gateway = ControlGateway(MagicMock())
    with pytest.raises(RuntimeError, match="npm run build"):
        create_app(gateway, static_dir=tmp_path)


def test_create_app_health_and_app_factory(tmp_path):
    static_dir = tmp_path / "webui" / "dist"
    static_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    gateway = ControlGateway(MagicMock())

    app = create_app(gateway, static_dir=static_dir)

    routes = {route.path for route in app.routes}
    assert "/api/v1/system/health" in routes
    assert "/api/v1/events" in routes
    assert any(getattr(route, "name", "") == "webui" for route in app.routes)


def test_cli_main_starts_runtime_thread(monkeypatch, tmp_path):
    """桌面路径必须显式启动 Runtime 线程（P0 回归测试）。

    单网卡自动选择是最常见配置：若 cli.main 只构造 Runtime 而不调用
    start()，循环线程永不运行、状态卡在 STARTING。interface 为 None 时
    start() 负责置 WAITING_INTERFACE。
    """
    from dm3c_ecat.desktop import cli

    class FakeRuntime:
        def __init__(self, interface):
            self.interface = interface
            self.start_calls = 0

        def start(self):
            self.start_calls += 1

        def snapshot(self):
            return {"state": "WAITING_INTERFACE"}

    monkeypatch.setattr(cli, "_static_dir", lambda: None)
    monkeypatch.setattr(cli, "resolve_default_interface", lambda: "fake-iface")
    monkeypatch.setattr(cli, "configure_logging", lambda log_file: tmp_path / "log")
    created: list[FakeRuntime] = []
    original_init = FakeRuntime.__init__

    def recording_init(self, interface):
        original_init(self, interface)
        created.append(self)

    FakeRuntime.__init__ = recording_init
    monkeypatch.setattr(cli, "Runtime", FakeRuntime)

    monkeypatch.setattr(
        cli, "run_desktop", lambda gateway, static_dir, *, open_window: 0
    )

    # cli.main 无参调用：--no-window 等默认参数由 argparse 填充。
    import sys

    monkeypatch.setattr(sys, "argv", ["ecat-desktop"])
    exit_code = cli.main()

    assert exit_code == 0
    assert len(created) == 1
    assert created[0].start_calls == 1
