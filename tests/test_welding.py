from __future__ import annotations

import pytest

from dm3c_ecat.device_profiles import WELDING_PROFILES, get_welding_profile
from dm3c_ecat.hmi import Runtime
from dm3c_ecat.welding import decode_welding_status, welding_packet


class FakeWeldingSlave:
    def __init__(self, output_size: int = 37, input_size: int = 37) -> None:
        self.output = bytearray(output_size)
        self.input = bytearray(input_size)
        self.man = WELDING_PROFILES[0].vendor
        self.id = WELDING_PROFILES[0].product


def test_megmeet_profile_matches_esi_and_protocol_table():
    profile = get_welding_profile(0xE000001B, 0x00000036)

    assert profile is WELDING_PROFILES[0]
    assert (profile.rx_pdo, profile.tx_pdo) == (0x1600, 0x1A00)
    assert (profile.rx_bytes, profile.tx_bytes) == (37, 37)
    assert (profile.command_bytes, profile.status_bytes) == (8, 14)


def test_welding_packet_places_command_bits_and_uint16_values():
    payload = welding_packet(
        start_welding=True,
        robot_ready=True,
        mode="separate",
        gas_test=True,
        wire_inch=False,
        wire_retract=True,
        touch_enable=True,
        job=49,
        current_or_speed=500,
        voltage_or_strength=300,
    )

    assert len(payload) == 37
    assert payload[:8] == bytes((0x13, 0x15, 49, 0, 0xF4, 0x01, 0x2C, 0x01))
    assert payload[8:] == bytes(29)


def test_welding_packet_rejects_unsafe_job():
    with pytest.raises(ValueError, match="JOB must be 0..49"):
        welding_packet(
            start_welding=False,
            robot_ready=False,
            mode="job",
            gas_test=False,
            wire_inch=False,
            wire_retract=False,
            touch_enable=False,
            job=50,
            current_or_speed=0,
            voltage_or_strength=0,
        )

def test_runtime_parameter_command_rejects_start_bit(runtime):
    runtime.welding_profile = WELDING_PROFILES[0]
    runtime.state = "OPERATIONAL"

    with pytest.raises(ValueError, match="use start_welding"):
        runtime.set_welding_command(
            start_welding=True,
            robot_ready=False,
            mode="job",
            gas_test=False,
            wire_inch=False,
            wire_retract=False,
            touch_enable=False,
            job=0,
            current_or_speed=0,
            voltage_or_strength=0,
        )


@pytest.mark.parametrize(
    ("communication_ready", "power_fault", "fault_code", "message"),
    [
        (False, False, 0, "communication is not ready"),
        (True, True, 0, "power reports a fault"),
        (True, False, 17, "fault code 17"),
    ],
)
def test_runtime_rejects_welding_start_when_interlock_is_open(
    runtime, communication_ready, power_fault, fault_code, message
):
    runtime.welding_profile = WELDING_PROFILES[0]
    runtime.state = "OPERATIONAL"
    runtime.welding_communication_ready = communication_ready
    runtime.welding_power_fault = power_fault
    runtime.welding_fault_code = fault_code

    runtime.set_welding_command(
        start_welding=False,
        robot_ready=True,
        mode="job",
        gas_test=False,
        wire_inch=False,
        wire_retract=False,
        touch_enable=False,
        job=0,
        current_or_speed=0,
        voltage_or_strength=0,
    )

    with pytest.raises(RuntimeError, match=message):
        runtime.start_welding()

    assert runtime.welding_start_welding is False


def test_decode_welding_status_matches_output_table():
    payload = bytearray(37)
    payload[0] = 0x65
    payload[1] = 7
    payload[3] = 1
    payload[4:6] = (1234).to_bytes(2, "little")
    payload[6:8] = (567).to_bytes(2, "little")
    payload[12:14] = (2800).to_bytes(2, "little")

    status = decode_welding_status(payload)

    assert status.arc_success is True
    assert status.welding is True
    assert status.power_fault is True
    assert status.communication_ready is True
    assert status.fault_code == 7
    assert status.touch_success is True
    assert status.actual_voltage == 1234
    assert status.actual_current == 567
    assert status.wire_speed == 2800


def test_runtime_welding_command_cycle_feedback_and_stop(runtime):
    slave = FakeWeldingSlave()
    master = runtime.master
    runtime.welding_profile = WELDING_PROFILES[0]
    runtime.welding_slave = slave
    runtime.state = "OPERATIONAL"
    runtime.welding_communication_ready = True
    master.receive_processdata.return_value = 3

    runtime.set_welding_command(
        start_welding=False,
        robot_ready=True,
        mode="pulse_unified",
        gas_test=True,
        wire_inch=False,
        wire_retract=False,
        touch_enable=True,
        job=3,
        current_or_speed=420,
        voltage_or_strength=320,
    )
    runtime.start_welding()

    assert runtime.welding_command_active is True
    assert runtime.welding_cycle() == 3
    assert slave.output[:8] == bytes((0x07, 0x11, 3, 0, 0xA4, 0x01, 0x40, 0x01))
    master.send_processdata.assert_called_once_with()

    slave.input[0] = 0x45
    slave.input[1] = 12
    slave.input[4:6] = (245).to_bytes(2, "little")
    slave.input[6:8] = (180).to_bytes(2, "little")
    slave.input[12:14] = (125).to_bytes(2, "little")
    runtime.welding_feedback()

    snapshot = runtime.snapshot()
    assert snapshot["deviceType"] == "welding"
    assert snapshot["hasWelding"] is True
    assert snapshot["weldingActualVoltage"] == 245
    assert snapshot["weldingActualCurrent"] == 180
    assert snapshot["weldingWireSpeed"] == 125
    assert snapshot["weldingFaultCode"] == 12
    assert snapshot["weldingArcSuccess"] is True
    assert snapshot["weldingCommunicationReady"] is True
    assert snapshot["weldingCommandActive"] is False
    assert slave.output[:8] == bytes(8)

    runtime.stop()

    assert runtime.welding_command_active is False
    assert slave.output[:8] == bytes(8)
    assert master.send_processdata.call_count == 2
