"""Supported EtherCAT drive identities and process-image requirements."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import pysoem

from .motion_modes import MODE_CSP, MODE_HM, MODE_PP, MODE_PV, ModePdo

LOGGER = logging.getLogger("ecat_test.profiles")

_VIRTUAL_ADAPTER_MARKERS = (
    "wan miniport",
    "wi-fi",
    "wifi",
    "wireless",
    "bluetooth",
    "virtual",
    "vpn",
    "loopback",
    "tunnel",
    "teredo",
    "atrust",
    "sangfor",
)


def _adapter_description(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _is_physical_adapter(description: object) -> bool:
    normalized = _adapter_description(description).casefold()
    return not any(marker in normalized for marker in _VIRTUAL_ADAPTER_MARKERS)


def enumerate_adapters() -> list[tuple[str, str, bool]]:
    try:
        raw_adapters = pysoem.find_adapters()
    except Exception as exc:  # pragma: no cover - depends on host drivers
        LOGGER.warning("EtherCAT adapter enumeration failed: %s", exc)
        return []
    return [
        (str(name), description_text, _is_physical_adapter(description_text))
        for name, description in raw_adapters
        for description_text in (_adapter_description(description),)
    ]


def resolve_default_interface() -> str | None:
    """Return an explicit interface or the only physical adapter.

    Multiple physical adapters are left for the HMI to choose explicitly.
    """
    explicit = os.environ.get("ECAT_INTERFACE")
    if explicit:
        return explicit

    adapters = enumerate_adapters()
    physical_adapters = [adapter for adapter in adapters if adapter[2]]
    if len(physical_adapters) == 1:
        name, description, _ = physical_adapters[0]
        LOGGER.info("Auto-selected physical EtherCAT adapter: %r (%s)", name, description)
        return name
    if len(physical_adapters) > 1:
        candidates = "; ".join(
            f"{name} ({description})" for name, description, _ in physical_adapters
        )
        LOGGER.info("Multiple physical EtherCAT adapters found; HMI selection required: %s", candidates)
    else:
        LOGGER.warning("No physical EtherCAT adapter found; waiting for HMI selection")
    return None

@dataclass(frozen=True, slots=True)
class DriveProfile:
    name: str
    vendor: int
    product: int
    rx_pdo: int
    tx_pdo: int
    rx_bytes: int
    tx_bytes: int
    mode: int = 3
    pp_rx_pdo: int = 0x1601
    pp_rx_bytes: int = 19
    pp_mode: int = 1
    mode_pdos: tuple[ModePdo, ...] = ()

    @property
    def io_map_bytes(self) -> int:
        return self.rx_bytes + self.tx_bytes

    def mode_pdo(self, mode: str) -> ModePdo | None:
        return next((item for item in self.mode_pdos if item.mode == mode), None)

    @property
    def supported_modes(self) -> tuple[str, ...]:
        return tuple(item.mode for item in self.mode_pdos)


DRIVE_PROFILES = (
    DriveProfile(
        "Leadshine DM3C-EC556",
        0x4321,
        0x8600,
        0x1602,
        0x1A00,
        15,
        19,
        mode_pdos=(
            ModePdo(MODE_PV, 0x1602, 15, "velocity"),
            ModePdo(MODE_PP, 0x1601, 19, "profile_position"),
            ModePdo(MODE_HM, 0x1603, 20, "homing"),
            ModePdo(MODE_CSP, 0x1600, 8, "csp", mode_in_pdo=False),
        ),
    ),
    DriveProfile(
        "KaiFull EC2SS3 / SSD60N",
        0x024B,
        0x0215,
        0x1602,
        0x1A00,
        15,
        23,
        mode_pdos=(
            ModePdo(MODE_PV, 0x1602, 15, "velocity"),
            ModePdo(MODE_PP, 0x1601, 19, "profile_position"),
            ModePdo(MODE_HM, 0x1603, 20, "homing"),
            ModePdo(
                MODE_CSP,
                0x1600,
                13,
                "csp",
                target_velocity_in_pdo=True,
            ),
        ),
    ),
)

PROFILES_BY_ID = {
    (profile.vendor, profile.product): profile for profile in DRIVE_PROFILES
}


def get_drive_profile(vendor: int, product: int) -> DriveProfile | None:
    return PROFILES_BY_ID.get((vendor, product))