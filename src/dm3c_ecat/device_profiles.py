"""Supported EtherCAT drive identities and process-image requirements."""

from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_INTERFACE = os.environ.get("ECAT_INTERFACE") or (
    r"\Device\NPF_{D9B4531B-7609-4733-BC7B-7194512C5F4D}"
)


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

    @property
    def io_map_bytes(self) -> int:
        return self.rx_bytes + self.tx_bytes


DRIVE_PROFILES = (
    DriveProfile("Leadshine DM3C-EC556", 0x4321, 0x8600, 0x1602, 0x1A00, 15, 19),
    DriveProfile("KaiFull EC2SS3 / SSD60N", 0x024B, 0x0215, 0x1602, 0x1A00, 15, 23),
)

PROFILES_BY_ID = {
    (profile.vendor, profile.product): profile for profile in DRIVE_PROFILES
}


def get_drive_profile(vendor: int, product: int) -> DriveProfile | None:
    return PROFILES_BY_ID.get((vendor, product))