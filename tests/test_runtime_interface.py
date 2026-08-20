from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from dm3c_ecat.device_profiles import DRIVE_PROFILES
import dm3c_ecat.hmi as hmi


def test_runtime_without_interface_waits_without_starting_thread(monkeypatch):
    monkeypatch.setattr(hmi.pysoem, "Master", lambda: MagicMock())

    runtime = hmi.Runtime(None)
    runtime.start()

    assert runtime.state == "WAITING_INTERFACE"
    assert runtime.snapshot()["connected"] is False
    assert runtime.thread.is_alive() is False


def test_enable_is_rejected_without_interface(monkeypatch):
    monkeypatch.setattr(hmi.pysoem, "Master", lambda: MagicMock())

    runtime = hmi.Runtime(None)

    with pytest.raises(RuntimeError, match="select an EtherCAT adapter"):
        runtime.enable()


def test_select_interface_restarts_runtime(monkeypatch):
    monkeypatch.setattr(hmi.pysoem, "Master", lambda: MagicMock())
    runtime = hmi.Runtime(None)
    runtime.start = MagicMock()

    runtime.select_interface(r"\\Device\NPF_{PHYSICAL}")

    assert runtime.interface == r"\\Device\NPF_{PHYSICAL}"
    assert runtime.state == "STARTING"
    runtime.start.assert_called_once_with()


def test_select_interface_is_rejected_while_enabled(monkeypatch):
    monkeypatch.setattr(hmi.pysoem, "Master", lambda: MagicMock())
    runtime = hmi.Runtime("test-iface")
    runtime.enable_requested = True

    with pytest.raises(RuntimeError, match="disable the drive"):
        runtime.select_interface("other-iface")


@pytest.mark.parametrize("motion_field", ["homing_active", "csp_move_active"])
def test_select_interface_is_rejected_during_homing_or_csp(runtime, motion_field):
    setattr(runtime, motion_field, True)

    with pytest.raises(RuntimeError, match="disable the drive"):
        runtime.select_interface("other-iface")


@pytest.mark.parametrize("motion_field", ["homing_active", "csp_move_active"])
def test_set_mode_is_rejected_during_homing_or_csp(runtime, motion_field):
    runtime.motion_mode = hmi.MODE_PV
    setattr(runtime, motion_field, True)

    with pytest.raises(RuntimeError, match="disable the drive"):
        runtime.set_mode(hmi.MODE_PP)


def test_pp_ramp_times_are_converted_to_profile_values(runtime):
    runtime.motion_mode = hmi.MODE_PP
    runtime.enable_requested = True
    runtime.enabled = True

    runtime.move_pp(1000, 2000, 0.5, 1.0)

    assert runtime.pp_acceleration == 4000
    assert runtime.pp_deceleration == 2000


def test_csp_and_homing_packets_match_profile_shapes():
    csp_without_mode = hmi.csp_packet(0x000F, 1234, mode_in_pdo=False)
    csp_with_mode = hmi.csp_packet(0x000F, 1234, mode=8, mode_in_pdo=True)
    csp_with_velocity = hmi.csp_packet(
        0x000F,
        1234,
        mode=8,
        mode_in_pdo=True,
        target_velocity=-55,
        target_velocity_in_pdo=True,
    )
    homing = hmi.homing_packet(0x001F, 35, 500, 100, 2000, -12)

    assert len(csp_without_mode) == 8
    assert len(csp_with_mode) == 9
    assert len(csp_with_velocity) == 13
    assert len(homing) == 20
    assert int.from_bytes(csp_with_mode[2:6], "little", signed=True) == 1234
    assert int.from_bytes(csp_with_velocity[9:13], "little", signed=True) == -55
    assert homing[2] == 35
    assert int.from_bytes(homing[15:19], "little", signed=True) == -12


def test_homing_start_bit_is_held_until_completion(runtime, process_slave):
    runtime.slave = process_slave
    runtime.profile = DRIVE_PROFILES[1]
    runtime.motion_mode = hmi.MODE_HM
    runtime.homing_pending = True

    runtime.cycle(0x000F, 0)
    first_controlword = int.from_bytes(process_slave.output[0:2], "little")
    runtime.cycle(0x000F, 0)
    second_controlword = int.from_bytes(process_slave.output[0:2], "little")

    assert first_controlword & hmi.HOMING_START
    assert second_controlword & hmi.HOMING_START
    assert runtime.homing_active is True


def test_runtime_rejects_mode_without_profile_pdo(runtime):
    runtime.profile = DRIVE_PROFILES[0]

    with pytest.raises(RuntimeError, match="CSV mode is not available"):
        runtime.set_mode(hmi.MODE_CSV)


def test_runtime_filters_profile_modes_by_6502(runtime):
    runtime.profile = DRIVE_PROFILES[1]
    runtime.slave = MagicMock()
    runtime.slave.sdo_read.return_value = b"\xA5\x00"

    runtime._read_mode_capabilities()

    assert runtime.available_modes() == (hmi.MODE_PV, hmi.MODE_PP)
    assert runtime.snapshot()["availableModes"] == [hmi.MODE_PV, hmi.MODE_PP]
    assert runtime.snapshot()["modeCapabilities"] == "0x00A5"
    with pytest.raises(RuntimeError, match="HM mode is not available"):
        runtime.set_mode(hmi.MODE_HM)


def test_runtime_fails_closed_when_6502_is_unavailable(runtime):
    runtime.profile = DRIVE_PROFILES[1]
    runtime.slave = MagicMock()
    runtime.slave.sdo_read.side_effect = RuntimeError("SDO abort")

    with pytest.raises(RuntimeError, match="0x6502"):
        runtime._read_mode_capabilities()


def test_homing_and_csp_commands_queue_motion(runtime):
    runtime.motion_mode = hmi.MODE_HM
    runtime.enable_requested = True
    runtime.enabled = True
    runtime.start_homing(35, 500, 100, 0.5, -10)

    assert runtime.homing_pending is True
    assert runtime.homing_method == 35
    assert runtime.homing_offset == -10

    runtime.stop_motion()
    runtime.motion_mode = hmi.MODE_CSP
    runtime.actual_position = 200
    runtime.move_csp(1200, 1.0)

    assert runtime.csp_move_pending is True
    assert runtime.csp_start_position == 200
    assert runtime.csp_target_position == 1200


def test_snapshot_exposes_profile_modes_and_motion_state(runtime):
    runtime.profile = DRIVE_PROFILES[0]
    runtime.motion_mode = hmi.MODE_HM
    runtime.homing_active = True

    snapshot = runtime.snapshot()

    assert snapshot["availableModes"] == [hmi.MODE_PV, hmi.MODE_PP, hmi.MODE_HM, hmi.MODE_CSP]
    assert snapshot["motionModeValue"] == 6
    assert snapshot["homingActive"] is True
    assert snapshot["moving"] is True