from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

import dm3c_ecat.device_profiles as device_profiles
from dm3c_ecat.motion_modes import MODE_CSP, MODE_HM, MODE_PP, MODE_PV


def test_default_interface_ignores_virtual_adapters(monkeypatch):
    monkeypatch.setattr(
        device_profiles.pysoem,
        "find_adapters",
        lambda: [
            (r"\\Device\NPF_{PHYSICAL}", "Realtek PCIe GbE Family Controller"),
            (r"\\Device\NPF_{WAN}", "WAN Miniport (IP)"),
            (r"\\Device\NPF_{WIFI}", "Intel(R) Wi-Fi 6E AX211"),
        ],
    )

    adapters = device_profiles.enumerate_adapters()

    assert adapters == [
        (r"\\Device\NPF_{PHYSICAL}", "Realtek PCIe GbE Family Controller", True),
        (r"\\Device\NPF_{WAN}", "WAN Miniport (IP)", False),
        (r"\\Device\NPF_{WIFI}", "Intel(R) Wi-Fi 6E AX211", False),
    ]
    assert device_profiles.resolve_default_interface() == r"\\Device\NPF_{PHYSICAL}"


def test_default_interface_requires_choice_for_multiple_physical_adapters(monkeypatch):
    monkeypatch.setattr(
        device_profiles.pysoem,
        "find_adapters",
        lambda: [
            (r"\\Device\NPF_{ONE}", "Realtek PCIe GbE Family Controller"),
            (r"\\Device\NPF_{TWO}", "Intel(R) Ethernet Controller"),
        ],
    )

    assert device_profiles.resolve_default_interface() is None


def test_explicit_interface_overrides_adapter_detection(monkeypatch):
    monkeypatch.setenv("ECAT_INTERFACE", r"\\Device\NPF_{EXPLICIT}")
    monkeypatch.setattr(
        device_profiles.pysoem,
        "find_adapters",
        lambda: (_ for _ in ()).throw(AssertionError("must not enumerate")),
    )

    assert device_profiles.resolve_default_interface() == r"\\Device\NPF_{EXPLICIT}"


def test_drive_profiles_expose_only_mapped_motion_modes():
    assert all(
        profile.supported_modes == (MODE_PV, MODE_PP, MODE_HM, MODE_CSP)
        for profile in device_profiles.DRIVE_PROFILES
    )


def test_csp_and_homing_profile_process_image_lengths():
    dm3c, kaifull = device_profiles.DRIVE_PROFILES

    assert dm3c.mode_pdo(MODE_HM).rx_bytes == 20
    assert kaifull.mode_pdo(MODE_HM).rx_bytes == 20
    assert dm3c.mode_pdo(MODE_CSP).rx_bytes == 8
    assert kaifull.mode_pdo(MODE_CSP).rx_bytes == 13
    assert kaifull.mode_pdo(MODE_CSP).target_velocity_in_pdo is True


def test_hauto_remote_io_profile_matches_esi_process_image():
    profile = device_profiles.get_remote_io_profile(0x01, 0x00010200)

    assert profile is device_profiles.REMOTE_IO_PROFILES[0]
    assert profile.rx_pdo == 0x1600
    assert profile.tx_pdo == 0x1A00
    assert (profile.rx_bytes, profile.tx_bytes) == (2, 2)
    assert (profile.input_channels, profile.output_channels) == (16, 16)


def test_decowell_remote_io_profile_matches_detected_module_pair():
    profile = device_profiles.get_remote_io_profile(0x00444543, 0x00000001)

    assert profile is device_profiles.REMOTE_IO_PROFILES[1]
    assert profile.rx_pdo == 0x1601
    assert profile.tx_pdo == 0x1A00
    assert (profile.rx_bytes, profile.tx_bytes) == (4, 8)
    assert (profile.input_channels, profile.output_channels) == (32, 32)
    assert profile.expected_module_ids == (0x7C, 0x7F)
    assert profile.module_init_commands == (
        (0x8000, 1, b"\x7C\x00"),
        (0x8010, 1, b"\x7F\x00"),
    )


def test_decowell_profile_expands_repeated_module_pair():
    profile = device_profiles.get_remote_io_profile(0x00444543, 0x00000001)

    expanded = profile.for_detected_modules((0x7C, 0x7F, 0x7C, 0x7F))

    assert expanded.name == "DECOWELL EX-203S + EX-313S 64DI/64DO"
    assert (expanded.rx_bytes, expanded.tx_bytes) == (8, 16)
    assert (expanded.input_channels, expanded.output_channels) == (64, 64)
    assert expanded.expected_module_ids == (0x7C, 0x7F, 0x7C, 0x7F)
    assert expanded.module_init_commands == (
        (0x8000, 1, b"\x7C\x00"),
        (0x8010, 1, b"\x7F\x00"),
        (0x8020, 1, b"\x7C\x00"),
        (0x8030, 1, b"\x7F\x00"),
    )


def test_remote_io_module_initialization_rejects_unexpected_modules():
    profile = device_profiles.get_remote_io_profile(0x00444543, 0x00000001)
    slave = MagicMock()
    slave.sdo_read.side_effect = lambda index, subindex: {
        (0xF050, 0): b"\x14",
        (0xF050, 1): b"\x7C\x00\x00\x00",
        (0xF050, 2): b"\x03\x00\x00\x00",
    }.get((index, subindex), b"\x00\x00\x00\x00")

    with pytest.raises(RuntimeError, match="do not match"):
        device_profiles.initialize_remote_io_modules(slave, profile)

    slave.sdo_write.assert_not_called()


def test_remote_io_module_initialization_writes_esi_slot_bytes():
    profile = device_profiles.get_remote_io_profile(0x00444543, 0x00000001)
    slave = MagicMock()
    slave.sdo_read.side_effect = lambda index, subindex: {
        (0xF050, 0): b"\x14",
        (0xF050, 1): b"\x7C\x00\x00\x00",
        (0xF050, 2): b"\x7F\x00\x00\x00",
    }.get((index, subindex), b"\x00\x00\x00\x00")

    device_profiles.initialize_remote_io_modules(slave, profile)

    assert slave.sdo_write.call_args_list == [
        call(0x8000, 1, b"\x7C\x00"),
        call(0x8010, 1, b"\x7F\x00"),
    ]


def test_remote_io_module_initialization_writes_repeated_slot_bytes():
    profile = device_profiles.get_remote_io_profile(0x00444543, 0x00000001)
    slave = MagicMock()
    detected = {
        (0xF050, 0): b"\x20",
        (0xF050, 1): b"\x7C\x00\x00\x00",
        (0xF050, 2): b"\x7F\x00\x00\x00",
        (0xF050, 3): b"\x7C\x00\x00\x00",
        (0xF050, 4): b"\x7F\x00\x00\x00",
    }
    slave.sdo_read.side_effect = lambda index, subindex: detected.get(
        (index, subindex), b"\x00\x00\x00\x00"
    )

    expanded = device_profiles.initialize_remote_io_modules(slave, profile)

    assert expanded.input_channels == 64
    assert expanded.output_channels == 64
    assert slave.sdo_write.call_args_list == [
        call(0x8000, 1, b"\x7C\x00"),
        call(0x8010, 1, b"\x7F\x00"),
        call(0x8020, 1, b"\x7C\x00"),
        call(0x8030, 1, b"\x7F\x00"),
    ]