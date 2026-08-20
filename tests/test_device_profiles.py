from __future__ import annotations

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