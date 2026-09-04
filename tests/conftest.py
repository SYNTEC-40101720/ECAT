"""Shared pytest fixtures for ECAT Test unit tests.

Tests cover the pure-logic methods of :class:`dm3c_ecat.hmi.Runtime`
(`set_mode`, `move_pp`, `jog`, `ramp_values`, `stop_motion`, `disable`)
and the byte-exact packet helpers. The loop thread is never started and no
EtherCAT hardware is touched: ``pysoem.Master`` is stubbed so constructing a
Runtime does not require a real adapter.
"""

from __future__ import annotations

from collections import deque
from typing import Any
from unittest.mock import MagicMock

import pytest


class FakeProcessSlave:
    def __init__(self, output_size: int = 15, input_size: int = 12) -> None:
        self.output = bytearray(output_size)
        self.input = bytearray(input_size)
        self.man = 0x4321
        self.id = 0x8600
        self.sdo_writes: list[tuple[int, int, bytes]] = []

    def sdo_write(self, index: int, subindex: int, value: bytes) -> None:
        self.sdo_writes.append((index, subindex, value))


class FakeProcessMaster:
    def __init__(self, slave: FakeProcessSlave) -> None:
        self.slaves = [slave]
        self.expected_wkc = 3
        self.receive_wkc = 3
        self.overlap_send_count = 0
        self.normal_send_count = 0
        self.receive_timeouts: list[int] = []

    def open(self, interface: str) -> None:
        del interface
        return None

    def close(self) -> None:
        return None

    def config_init(self) -> int:
        return 1

    def send_overlap_processdata(self) -> None:
        self.overlap_send_count += 1

    def send_processdata(self) -> None:
        self.normal_send_count += 1

    def receive_processdata(self, timeout_us: int) -> int:
        self.receive_timeouts.append(timeout_us)
        return self.receive_wkc


@pytest.fixture(autouse=True)
def _no_adapter_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests off real hardware: never auto-detect an adapter.

    `device_profiles.resolve_default_interface` is only called by CLI entry
    points, not at import or Runtime construction. Clear ECAT_INTERFACE so a
    developer's local setting cannot leak into a test run.
    """
    monkeypatch.delenv("ECAT_INTERFACE", raising=False)


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A Runtime with pysoem.Master stubbed out and the loop thread NOT started.

    The returned object behaves like a freshly-constructed Runtime: state is
    STARTING, motion_mode is PV, slave/profile are None. Callers can drive the
    pure-logic API (enable, set_mode, move_pp, jog, ...) directly. To exercise
    PP paths set ``runtime.motion_mode = MODE_PP`` and ``runtime.profile`` /
    ``runtime.slave`` as needed; most guards only inspect Python state.
    """
    # Stub pysoem.Master BEFORE Runtime.__init__ calls it.
    import dm3c_ecat.hmi as hmi

    fake_master = MagicMock(name="pysoem.Master")
    fake_master.expected_wkc = 3
    monkeypatch.setattr(hmi.pysoem, "Master", lambda: fake_master)

    rt = hmi.Runtime("test-iface")
    # Thread is created (daemon) but never started -> no background loop.
    return rt


@pytest.fixture
def runtime_no_assertions() -> Any:
    """Alias kept for clarity; prefer ``runtime``."""
    return None


@pytest.fixture
def process_slave() -> FakeProcessSlave:
    return FakeProcessSlave()


@pytest.fixture
def process_master(process_slave: FakeProcessSlave) -> FakeProcessMaster:
    return FakeProcessMaster(process_slave)


@pytest.fixture(autouse=True)
def _isolate_log_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each test a fresh LOG_BUFFER so assertions on captured logs don't
    bleed across tests and never affect a real file handler."""
    import dm3c_ecat.hmi as hmi

    monkeypatch.setattr(hmi, "LOG_BUFFER", deque(maxlen=400))
