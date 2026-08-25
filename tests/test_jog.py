from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from dm3c_ecat.jog import (
    DRIVE_FAULT,
    MAX_VELOCITY,
    output_packet,
    statusword_from_input,
    statusword_from_overlap_output,
    wait_status,
)
import dm3c_ecat.jog as jog


class FakeJogSlave:
    def __init__(self) -> None:
        self.output = bytearray(15)
        self.input = bytearray(19)


class FakeJogMaster:
    def __init__(self, slave: FakeJogSlave, receive_wkc: int = 3) -> None:
        self.slave = slave
        self.expected_wkc = 3
        self.receive_wkc = receive_wkc
        self.overlap_send_count = 0
        self.normal_send_count = 0

    def send_overlap_processdata(self) -> None:
        self.overlap_send_count += 1

    def send_processdata(self) -> None:
        self.normal_send_count += 1

    def receive_processdata(self, _timeout_us: int) -> int:
        return self.receive_wkc


def test_statusword_is_read_from_input_process_image():
    slave = FakeJogSlave()
    slave.output[2:4] = (0x0000).to_bytes(2, "little")
    slave.input[2:4] = (0x0021).to_bytes(2, "little")

    assert statusword_from_input(slave) == 0x0021
    assert statusword_from_overlap_output(slave) == 0x0021


def test_wait_status_uses_overlap_process_data_and_checks_wkc():
    slave = FakeJogSlave()
    slave.input[2:4] = (0x0021).to_bytes(2, "little")
    master = FakeJogMaster(slave)

    status = wait_status(master, slave, 0x0006, 0x006F, 0x0021)

    assert status == 0x0021
    assert master.overlap_send_count == 1
    assert master.normal_send_count == 0


def test_wait_status_rejects_wkc_mismatch():
    slave = FakeJogSlave()
    master = FakeJogMaster(slave, receive_wkc=1)

    with pytest.raises(RuntimeError, match="WKC mismatch"):
        wait_status(master, slave, 0x0006, 0x006F, 0x0021)


def test_wait_status_rejects_drive_fault():
    slave = FakeJogSlave()
    slave.input[2:4] = DRIVE_FAULT.to_bytes(2, "little")
    master = FakeJogMaster(slave)

    with pytest.raises(RuntimeError, match="drive fault"):
        wait_status(master, slave, 0x0006, 0x006F, 0x0021)


def test_output_packet_is_bounded_by_cli_velocity_limit():
    assert MAX_VELOCITY == 100_000
    assert len(output_packet(0x000F, MAX_VELOCITY)) == 15
    assert len(output_packet(0x000F, -MAX_VELOCITY)) == 15


def test_cli_rejects_velocity_above_limit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["ecat-jog", str(MAX_VELOCITY + 1), "1", "test-iface", "--confirm-jog"],
    )

    with pytest.raises(SystemExit) as error:
        jog.main()

    assert error.value.code == 2


def test_wait_status_propagates_master_wkc_without_normal_send():
    slave = FakeJogSlave()
    slave.input[2:4] = (0x0021).to_bytes(2, "little")
    master = MagicMock()
    master.expected_wkc = 3
    master.receive_processdata.return_value = 3

    wait_status(master, slave, 0x0006, 0x006F, 0x0021)

    master.send_overlap_processdata.assert_called_once_with()
    master.send_processdata.assert_not_called()