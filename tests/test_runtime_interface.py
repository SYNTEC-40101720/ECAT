from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from dm3c_ecat.device_profiles import (
    DRIVE_PROFILES,
    REMOTE_IO_PROFILES,
    WELDING_PROFILES,
    get_remote_io_profile,
)
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


def test_enable_drive_waits_for_each_cia402_state(runtime, monkeypatch):
    statuses = iter((0x0000, 0x0021, 0x0023, 0x0027))
    runtime.expected_wkc = 3
    runtime.cycle = MagicMock(return_value=3)
    runtime.feedback = MagicMock(
        side_effect=lambda: setattr(runtime, "statusword", next(statuses))
    )
    monkeypatch.setattr(hmi.time, "sleep", lambda _delay: None)

    runtime.enable_drive()

    assert runtime.enabled is True
    assert [item.args[0] for item in runtime.cycle.call_args_list] == [
        0x0006,
        0x0006,
        0x0007,
        0x000F,
    ]


def test_enable_drive_times_out_if_state_never_changes(runtime, monkeypatch):
    runtime.expected_wkc = 3
    runtime.cycle = MagicMock(return_value=3)
    runtime.feedback = MagicMock(
        side_effect=lambda: setattr(runtime, "statusword", 0x0000)
    )
    monkeypatch.setattr(hmi.time, "sleep", lambda _delay: None)

    with pytest.raises(RuntimeError, match="Ready to switch on"):
        runtime.enable_drive()

    assert runtime.enabled is False
    assert runtime.cycle.call_count == hmi.CIA402_STATE_RETRIES


def test_enable_drive_rejects_fault_statusword(runtime):
    runtime.expected_wkc = 3
    runtime.cycle = MagicMock(return_value=3)
    runtime.feedback = MagicMock(
        side_effect=lambda: setattr(runtime, "statusword", hmi.CIA402_FAULT)
    )

    with pytest.raises(RuntimeError, match="drive fault during enable"):
        runtime.enable_drive()

    assert runtime.enabled is False
    runtime.cycle.assert_called_once_with(0x0006, 0)


def test_runtime_loop_latches_drive_fault(runtime):
    runtime.profile = DRIVE_PROFILES[0]
    runtime.drive_slave = MagicMock()
    runtime.expected_wkc = 3
    runtime.running = True
    runtime.enable_requested = True
    runtime.enabled = True
    runtime.configure = MagicMock()

    def finish_cycle(_controlword, _velocity):
        runtime.running = False
        return 3

    runtime.cycle = MagicMock(side_effect=finish_cycle)
    runtime.feedback = MagicMock(
        side_effect=lambda: setattr(runtime, "statusword", hmi.CIA402_FAULT)
    )

    runtime.loop()

    assert runtime.enable_requested is False
    assert runtime.enabled is False
    assert runtime.state == "ERROR"
    assert runtime.message == "Drive fault reported by statusword: 0x0008"


def test_select_interface_restarts_runtime(monkeypatch):
    monkeypatch.setattr(hmi.pysoem, "Master", lambda: MagicMock())
    runtime = hmi.Runtime(None)
    runtime.start = MagicMock()

    runtime.select_interface(r"\\Device\NPF_{PHYSICAL}")

    assert runtime.interface == r"\\Device\NPF_{PHYSICAL}"
    assert runtime.state == "STARTING"
    runtime.start.assert_called_once_with()


def test_configure_reports_missing_slave_without_affecting_adapter_selection(runtime):
    runtime.master.config_init.return_value = 0

    with pytest.raises(
        RuntimeError, match="no EtherCAT slave detected on the selected interface"
    ):
        runtime.configure()


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


@pytest.mark.parametrize(
    ("device_field", "profile", "active_field"),
    [
        ("io_profile", REMOTE_IO_PROFILES[1], "io_output_mask"),
        ("welding_profile", WELDING_PROFILES[0], "welding_command_active"),
        ("welding_profile", WELDING_PROFILES[0], "welding_active"),
    ],
)
def test_mode_switch_rejects_active_mixed_bus_outputs(
    runtime, device_field, profile, active_field
):
    runtime.profile = DRIVE_PROFILES[0]
    setattr(runtime, device_field, profile)
    setattr(runtime, active_field, 1 if active_field == "io_output_mask" else True)
    runtime.motion_mode = hmi.MODE_PV
    runtime.pending_mode = hmi.MODE_PP

    with pytest.raises(RuntimeError, match="cannot perform mode switch"):
        runtime.switch_mode(hmi.MODE_PP)


def test_mode_switch_rejects_active_drive_io_and_welding(runtime):
    runtime.profile = DRIVE_PROFILES[0]
    runtime.io_profile = REMOTE_IO_PROFILES[0]
    runtime.welding_profile = WELDING_PROFILES[0]
    runtime.io_output_mask = 1
    runtime.welding_active = True
    runtime.pending_mode = hmi.MODE_PP

    with pytest.raises(RuntimeError, match="cannot perform mode switch"):
        runtime.switch_mode(hmi.MODE_PP)


def test_mode_switch_sends_zero_frame_and_requires_strict_wkc(runtime):
    runtime.profile = DRIVE_PROFILES[0]
    runtime.io_profile = REMOTE_IO_PROFILES[0]
    runtime.drive_slave = MagicMock()
    runtime.io_slave = MagicMock()
    runtime.drive_slave.output = bytearray(runtime.profile.rx_bytes)
    runtime.io_slave.output = bytearray(runtime.io_profile.rx_bytes)
    runtime.drive_slave.input = bytearray(runtime.profile.tx_bytes)
    runtime.io_slave.input = bytearray(runtime.io_profile.tx_bytes)
    runtime.expected_wkc = 3
    runtime._master_open = True
    runtime.cycle = MagicMock(return_value=3)
    runtime.motion_mode = hmi.MODE_PV
    runtime.master.config_init.return_value = 1
    runtime.master.state_check.side_effect = [
        hmi.pysoem.PREOP_STATE,
        hmi.pysoem.SAFEOP_STATE,
        hmi.pysoem.OP_STATE,
    ]
    runtime._identify_slaves = MagicMock()
    runtime._read_mode_capabilities = MagicMock()
    runtime.configure_process_data = MagicMock()

    runtime.switch_mode(hmi.MODE_PP)

    runtime.cycle.assert_called_once_with(0x0006, 0)
    assert bytes(runtime.io_slave.output) == b"\x00\x00"


def test_mode_switch_wkc_failure_latches_error_before_preop(runtime):
    runtime.profile = DRIVE_PROFILES[0]
    runtime.drive_slave = MagicMock()
    runtime.expected_wkc = 3
    runtime._master_open = True
    runtime.cycle = MagicMock(return_value=2)
    runtime.motion_mode = hmi.MODE_PV
    runtime.pending_mode = hmi.MODE_PP

    with pytest.raises(RuntimeError, match="safety interlock failed"):
        runtime.switch_mode(hmi.MODE_PP)

    assert runtime.state == "ERROR"
    runtime.master.state_check.assert_not_called()


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


def test_drive_pdo_assignment_readback_rejects_mismatch(runtime):
    drive = MagicMock()
    drive.sdo_read.side_effect = lambda index, subindex: {
        (0x1C12, 0): b"\x01",
        (0x1C12, 1): b"\x01\x16",
        (0x1C13, 0): b"\x01",
        (0x1C13, 1): b"\x00\x1A",
    }[(index, subindex)]
    runtime.drive_slave = drive
    runtime.profile = DRIVE_PROFILES[0]

    with pytest.raises(RuntimeError, match="0x1C12:01 mismatch"):
        runtime._validate_drive_pdo_assignments(0x1602)


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


def test_csp_trajectory_accepts_limits_and_reverse_direction(runtime):
    runtime.profile = DRIVE_PROFILES[1]
    runtime.motion_mode = hmi.MODE_CSP
    runtime.enable_requested = True
    runtime.enabled = True
    runtime.actual_position = 10_000

    runtime.move_csp(0, 1.0)

    assert runtime.csp_start_position == 10_000
    assert runtime.csp_target_position == 0


@pytest.mark.parametrize(
    ("target", "duration", "message"),
    [
        (hmi.CSP_MAX_VELOCITY + 1, 1.0, "velocity limit"),
        (50, 0.01, "acceleration limit"),
    ],
)
def test_csp_trajectory_rejects_derived_limits(runtime, target, duration, message):
    runtime.profile = DRIVE_PROFILES[1]
    runtime.motion_mode = hmi.MODE_CSP
    runtime.enable_requested = True
    runtime.enabled = True

    with pytest.raises(ValueError, match=message):
        runtime.move_csp(target, duration)

    assert runtime.csp_move_pending is False


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf")])
def test_csp_rejects_zero_negative_and_non_finite_duration(runtime, duration):
    runtime.profile = DRIVE_PROFILES[1]
    runtime.motion_mode = hmi.MODE_CSP
    runtime.enable_requested = True
    runtime.enabled = True

    with pytest.raises(ValueError):
        runtime.move_csp(100, duration)


def test_csp_following_error_protection_latches_error_and_clears_command(runtime):
    runtime.profile = DRIVE_PROFILES[1]
    runtime.drive_slave = MagicMock()
    runtime.expected_wkc = 3
    runtime.running = True
    runtime.enable_requested = True
    runtime.enabled = True
    runtime.motion_mode = hmi.MODE_CSP
    runtime.configure = MagicMock()
    runtime.cycle = MagicMock(return_value=3)
    runtime.feedback = MagicMock(
        side_effect=lambda: setattr(runtime, "actual_position", 2_000)
    )
    runtime.move_csp(0, 1.0)
    runtime.csp_protection_active = True
    runtime.csp_move_active = True
    runtime.csp_command_position = 0

    runtime.loop()

    assert runtime.state == "ERROR"
    assert runtime.enable_requested is False
    assert runtime.csp_move_active is False
    assert "following error exceeded" in runtime.message


def test_csp_cycle_protection_uses_virtual_time(runtime):
    runtime.csp_protection_active = True
    runtime.csp_move_active = True
    runtime.csp_command_position = 0
    runtime.actual_position = 0
    runtime.csp_last_cycle_at = 10.0

    assert runtime._check_csp_protection(10.0 + hmi.CSP_MAX_CYCLE_TIME) is None
    runtime.csp_last_cycle_at = 10.0
    assert runtime._check_csp_protection(10.0 + hmi.CSP_MAX_CYCLE_TIME + 0.001)


def test_snapshot_exposes_profile_modes_and_motion_state(runtime):
    runtime.profile = DRIVE_PROFILES[0]
    runtime.motion_mode = hmi.MODE_HM
    runtime.homing_active = True

    snapshot = runtime.snapshot()

    assert snapshot["availableModes"] == [hmi.MODE_PV, hmi.MODE_PP, hmi.MODE_HM, hmi.MODE_CSP]
    assert snapshot["motionModeValue"] == 6
    assert snapshot["homingActive"] is True
    assert snapshot["moving"] is True


def test_remote_io_reads_inputs_and_writes_output_bits(
    runtime, process_slave, process_master
):
    process_slave.output = bytearray(2)
    process_slave.input = bytearray(b"\x05\x80")
    runtime.io_profile = REMOTE_IO_PROFILES[0]
    runtime.slave = process_slave
    runtime.master = process_master
    runtime.expected_wkc = 3
    runtime._master_open = True
    runtime.state = "OPERATIONAL"

    runtime.set_digital_output(0, True)
    runtime.set_digital_output(15, True)

    assert runtime.io_output_mask == 0x8001
    assert runtime.io_cycle() == 3
    assert bytes(process_slave.output) == b"\x01\x80"
    assert runtime.read_io_inputs() == 0x8005
    assert runtime.snapshot()["ioInputMask"] == 0x8005
    assert runtime.snapshot()["ioOutputMask"] == 0x8001

    runtime.stop_motion()
    assert runtime.io_output_mask == 0
    assert process_master.normal_send_count == 2

    runtime.set_digital_output(0, True)
    runtime.stop()
    assert runtime.io_output_mask == 0
    assert process_master.normal_send_count == 3


def test_remote_io_rejects_invalid_output_channel(runtime):
    runtime.io_profile = REMOTE_IO_PROFILES[0]
    runtime.state = "OPERATIONAL"

    with pytest.raises(ValueError, match="0..15"):
        runtime.set_digital_output(16, True)


def test_partial_wkc_is_only_allowed_for_devices_with_flag(runtime):
    runtime.expected_wkc = 3
    runtime.wkc = 1

    # HAU TO and Solidot both have allow_partial_wkc=True
    runtime.io_profile = REMOTE_IO_PROFILES[0]
    assert runtime._process_wkc_is_valid(pure_io=True) is True
    assert runtime._process_wkc_is_valid() is False

    runtime.io_profile = REMOTE_IO_PROFILES[2]
    assert runtime._process_wkc_is_valid(pure_io=True) is True

    # DECOWELL does not allow partial WKC
    runtime.io_profile = REMOTE_IO_PROFILES[1]
    assert runtime._process_wkc_is_valid(pure_io=True) is False


def test_decowell_io_initializes_modules_before_mapping(runtime):
    io_profile = get_remote_io_profile(0x00444543, 0x00000001)
    io_slave = MagicMock()
    io_slave.state = hmi.pysoem.PREOP_STATE
    io_slave.output = bytearray(io_profile.rx_bytes)
    io_slave.input = bytearray(io_profile.tx_bytes)
    io_slave.sdo_read.side_effect = lambda index, subindex: {
        (0xF050, 0): b"\x14",
        (0xF050, 1): b"\x7C\x00\x00\x00",
        (0xF050, 2): b"\x7F\x00\x00\x00",
    }.get((index, subindex), b"\x00\x00\x00\x00")
    runtime.io_profile = io_profile
    runtime.io_slave = io_slave
    runtime.master.state_check.return_value = hmi.pysoem.PREOP_STATE
    runtime.master.config_map.return_value = io_profile.io_map_bytes

    runtime.configure_io_process_data()

    assert io_slave.sdo_write.call_args_list == [
        call(0x8000, 1, b"\x7C\x00"),
        call(0x8010, 1, b"\x7F\x00"),
    ]
    runtime.master.config_map.assert_called_once_with()


def test_solidot_ec4_mapping_accepts_only_known_fixed_pdo_error(runtime):
    io_profile = get_remote_io_profile(0x00884443, 0x00000004, 0x00000001)
    io_slave = MagicMock()
    io_slave.output = bytearray(2)
    io_slave.input = bytearray(2)
    runtime.io_profile = io_profile
    runtime.io_slave = io_slave
    runtime.master.slaves = [io_slave]
    runtime.master.config_map.side_effect = hmi.pysoem.ConfigMapError(
        [
            hmi.pysoem.SdoError(
                1,
                0x1C00,
                0,
                0x06020000,
                "The object does not exist in the object directory",
            )
        ]
    )

    runtime.configure_io_process_data()

    assert runtime.expected_wkc == 3
    runtime.master.config_map.assert_called_once_with()


def test_solidot_ec4_mapping_does_not_hide_other_errors(runtime):
    io_profile = get_remote_io_profile(0x00884443, 0x00000004, 0x00000001)
    io_slave = MagicMock()
    io_slave.output = bytearray(2)
    io_slave.input = bytearray(2)
    runtime.io_profile = io_profile
    runtime.io_slave = io_slave
    runtime.master.slaves = [io_slave]
    runtime.master.config_map.side_effect = hmi.pysoem.ConfigMapError(
        [hmi.pysoem.SdoError(1, 0x1C12, 0, 0x06010000, "mapping failed")]
    )

    with pytest.raises(hmi.pysoem.ConfigMapError):
        runtime.configure_io_process_data()


def test_remote_io_requests_safeop_process_cycle_before_op(runtime):
    runtime.io_profile = REMOTE_IO_PROFILES[0]
    runtime.io_slave = MagicMock()
    runtime.io_slave.output = bytearray(REMOTE_IO_PROFILES[0].rx_bytes)
    runtime.io_slave.input = bytearray(REMOTE_IO_PROFILES[0].tx_bytes)
    runtime.expected_wkc = 3
    runtime.master.state_check.side_effect = [
        hmi.pysoem.SAFEOP_STATE,
        hmi.pysoem.OP_STATE,
    ]
    runtime.master.receive_processdata.return_value = 3
    runtime.io_output_mask = 0x8001

    runtime.request_io_operational()

    assert runtime.master.state_check.call_args_list == [
        call(hmi.pysoem.SAFEOP_STATE, 200_000),
        call(hmi.pysoem.OP_STATE, 500_000),
    ]
    # io_cycle is called once at SAFE-OP and once after entering OP.
    assert runtime.master.send_processdata.call_count == 2
    assert runtime.master.receive_processdata.call_count == 2
    assert runtime.io_slave.output == b"\x00\x00"


def test_decowell_runtime_expands_repeated_modules_and_handles_high_bits(runtime):
    io_profile = get_remote_io_profile(0x00444543, 0x00000001)
    io_slave = MagicMock()
    io_slave.state = hmi.pysoem.PREOP_STATE
    io_slave.output = bytearray(8)
    io_slave.input = bytearray(16)
    detected = {
        (0xF050, 0): b"\x20",
        (0xF050, 1): b"\x7C\x00\x00\x00",
        (0xF050, 2): b"\x7F\x00\x00\x00",
        (0xF050, 3): b"\x7C\x00\x00\x00",
        (0xF050, 4): b"\x7F\x00\x00\x00",
    }
    io_slave.sdo_read.side_effect = lambda index, subindex: detected.get(
        (index, subindex), b"\x00\x00\x00\x00"
    )
    runtime.io_profile = io_profile
    runtime.io_slave = io_slave
    runtime.master.state_check.return_value = hmi.pysoem.PREOP_STATE
    runtime.master.config_map.return_value = 24
    runtime.master.receive_processdata.return_value = 3

    runtime.configure_io_process_data()
    runtime.state = "OPERATIONAL"
    runtime.set_digital_output(63, True)
    io_slave.input = b"\x00\x00\x00\x00\x00\x00\x00\x80" + b"\x00" * 8

    assert runtime.io_cycle() == 3
    assert bytes(io_slave.output) == b"\x00\x00\x00\x00\x00\x00\x00\x80"
    assert runtime.read_io_inputs() == 1 << 63
    snapshot = runtime.snapshot()
    assert snapshot["ioInputChannels"] == 64
    assert snapshot["ioOutputChannels"] == 64
    assert snapshot["ioInputMaskHex"] == "0x8000000000000000"
    assert snapshot["ioOutputMaskHex"] == "0x8000000000000000"


def test_runtime_keeps_drive_and_remote_io_on_the_same_bus(runtime):
    drive_slave = MagicMock()
    drive_slave.man = DRIVE_PROFILES[1].vendor
    drive_slave.id = DRIVE_PROFILES[1].product
    drive_slave.output = bytearray(DRIVE_PROFILES[1].rx_bytes)
    drive_slave.input = bytearray(DRIVE_PROFILES[1].tx_bytes)
    drive_slave.sdo_read.side_effect = lambda index, subindex: {
        (0x6502, 0): b"\xA5\x00",
        (0x1C12, 0): b"\x01",
        (0x1C12, 1): b"\x02\x16",
        (0x1C13, 0): b"\x01",
        (0x1C13, 1): b"\x00\x1A",
    }.get((index, subindex), b"\x00")
    io_slave = MagicMock()
    io_slave.man = REMOTE_IO_PROFILES[0].vendor
    io_slave.id = REMOTE_IO_PROFILES[0].product
    io_slave.output = bytearray(REMOTE_IO_PROFILES[0].rx_bytes)
    io_slave.input = bytearray(REMOTE_IO_PROFILES[0].tx_bytes)

    runtime.master.slaves = [drive_slave, io_slave]
    runtime.master.config_init.return_value = 2
    runtime.master.config_overlap_map.return_value = 42
    runtime.master.expected_wkc = 4
    runtime.master.state_check.side_effect = [
        hmi.pysoem.SAFEOP_STATE,
        hmi.pysoem.OP_STATE,
    ]

    runtime.configure()
    runtime.state = "ENABLED"
    runtime.set_digital_output(0, True)
    runtime.cycle(0x000F, 123)

    assert runtime.profile is DRIVE_PROFILES[1]
    assert runtime.io_profile is REMOTE_IO_PROFILES[0]
    assert runtime.slave is drive_slave
    assert runtime.snapshot()["deviceType"] == "mixed"
    assert runtime.snapshot()["devices"] == [
        DRIVE_PROFILES[1].name,
        REMOTE_IO_PROFILES[0].name,
    ]
    assert bytes(io_slave.output) == b"\x01\x00"
    runtime.read_io_inputs()
    assert runtime.master.send_overlap_processdata.called


def test_drive_loop_latches_wkc_loss_and_transmits_final_safe_frame(runtime):
    runtime.profile = DRIVE_PROFILES[0]
    runtime.drive_slave = MagicMock()
    runtime.configure = MagicMock()
    runtime.expected_wkc = 3
    runtime.cycle = MagicMock(side_effect=[2, 3])
    runtime.feedback = MagicMock()
    runtime.master.close = MagicMock()

    runtime.loop()

    assert runtime.running is False
    assert runtime.enabled is False
    assert runtime.enable_requested is False
    assert runtime.state == "ERROR"
    assert runtime.message == "Process-data WKC mismatch: 2/3"
    assert runtime.cycle.call_args_list == [call(0x0006, 0), call(0x0006, 0)]
    runtime.master.close.assert_called_once_with()


def test_pure_welding_loop_rejects_partial_wkc(runtime):
    runtime.welding_profile = WELDING_PROFILES[0]
    runtime.welding_slave = MagicMock()
    runtime.configure = MagicMock()
    runtime.expected_wkc = 3
    runtime.welding_cycle = MagicMock(side_effect=[1, 3])
    runtime.welding_feedback = MagicMock()
    runtime.master.close = MagicMock()

    runtime.loop()

    assert runtime.running is False
    assert runtime.welding_command_active is False
    assert runtime.state == "ERROR"
    assert runtime.message == "Welding process-data WKC mismatch: 1/3"
    assert runtime.welding_cycle.call_count == 2
    runtime.master.close.assert_called_once_with()


def test_pure_io_loop_latches_wkc_loss(runtime):
    runtime.io_profile = REMOTE_IO_PROFILES[1]
    runtime.io_slave = MagicMock()
    runtime.configure = MagicMock()
    runtime.expected_wkc = 3
    runtime.io_cycle = MagicMock(return_value=0)
    runtime.read_io_inputs = MagicMock()
    runtime.master.close = MagicMock()

    runtime.loop()

    assert runtime.running is False
    assert runtime.io_output_mask == 0
    assert runtime.state == "ERROR"
    assert "process-data exchange lost" in runtime.message
    runtime.master.close.assert_called_once_with()


def test_hauto_pure_io_loop_accepts_positive_partial_wkc(runtime):
    runtime.io_profile = REMOTE_IO_PROFILES[0]
    runtime.io_slave = MagicMock()
    runtime.configure = MagicMock()
    runtime.expected_wkc = 3

    def complete_one_cycle() -> int:
        runtime.running = False
        return 1

    runtime.io_cycle = MagicMock(side_effect=complete_one_cycle)
    runtime.read_io_inputs = MagicMock()
    runtime.master.close = MagicMock()

    runtime.loop()

    assert runtime.state == "OPERATIONAL"
    assert runtime.message == "Digital I/O ready; partial WKC 1/3"
    assert runtime.io_cycle.call_count == 2
    runtime.master.close.assert_called_once_with()


def test_safe_output_failure_latches_diagnostic_error(runtime):
    runtime.profile = DRIVE_PROFILES[0]
    runtime.drive_slave = MagicMock()
    runtime.state = "ENABLED"
    runtime.command = 100
    runtime.expected_wkc = 3
    runtime._master_open = True
    runtime.cycle = MagicMock(side_effect=RuntimeError("transport down"))

    runtime.stop_motion()

    assert runtime.running is False
    assert runtime.state == "ERROR"
    assert "Safe output transmission failed" in runtime.message
    assert "transport down" in runtime.message


def test_runtime_thread_stops_after_cycle_exception(runtime):
    runtime.profile = DRIVE_PROFILES[0]
    runtime.drive_slave = MagicMock()
    runtime.configure = MagicMock()
    runtime.cycle = MagicMock(side_effect=RuntimeError("cycle aborted"))
    runtime.master.close = MagicMock()

    runtime.start()
    runtime.thread.join(timeout=1)

    assert runtime.thread.is_alive() is False
    assert runtime.running is False
    assert runtime.state == "ERROR"
    assert "cycle aborted" in runtime.message
    runtime.master.close.assert_called_once_with()