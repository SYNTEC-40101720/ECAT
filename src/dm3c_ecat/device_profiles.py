"""Supported EtherCAT drive identities and process-image requirements."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace

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
    revision: int | None = None
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


@dataclass(frozen=True, slots=True)
class WeldingProfile:
    name: str
    vendor: int
    product: int
    rx_pdo: int
    tx_pdo: int
    rx_bytes: int
    tx_bytes: int
    command_bytes: int = 8
    status_bytes: int = 14

    @property
    def io_map_bytes(self) -> int:
        return self.rx_bytes + self.tx_bytes


@dataclass(frozen=True, slots=True)
class RemoteIoProfile:
    name: str
    vendor: int
    product: int
    rx_pdo: int
    tx_pdo: int
    rx_bytes: int
    tx_bytes: int
    input_channels: int
    output_channels: int
    revision: int | None = None
    expected_module_ids: tuple[int, ...] = ()
    module_init_commands: tuple[tuple[int, int, bytes], ...] = ()
    module_slot_stride: int = 0x10
    allow_partial_wkc: bool = False
    tolerated_mapping_sdo_errors: tuple[tuple[int, int, int], ...] = ()
    # "sdo_slot" (private per-slot SDO, e.g. DECOWELL 0x8000) or
    # "f030_array" (ETG.5001 standard 0xF030 Configured Module Ident List)
    module_config_protocol: str = "sdo_slot"
    # Extra PDO bytes consumed by the coupler itself (e.g. XB6 0x16FF/0x1AFF = 2 bytes each)
    coupler_rx_bytes: int = 0
    coupler_tx_bytes: int = 0

    @property
    def io_map_bytes(self) -> int:
        return self.rx_bytes + self.tx_bytes


    def for_detected_modules(
        self, module_ids: tuple[int, ...]
    ) -> "RemoteIoProfile":
        if not self.module_init_commands or not self.expected_module_ids:
            return self
        group_size = len(self.expected_module_ids)
        if (
            not module_ids
            or len(module_ids) % group_size != 0
            or module_ids != self.expected_module_ids * (len(module_ids) // group_size)
        ):
            expected = ", ".join(
                f"0x{module_id:08X}" for module_id in self.expected_module_ids
            )
            actual = ", ".join(f"0x{module_id:08X}" for module_id in module_ids) or "none"
            raise RuntimeError(
                f"detected modules do not match {self.name}: "
                f"expected repeated sequence [{expected}], got [{actual}]"
            )

        group_count = len(module_ids) // group_size
        if group_count == 1:
            return self
        slot_group_stride = group_size * self.module_slot_stride
        module_init_commands = tuple(
            (
                index + group_index * slot_group_stride,
                subindex,
                value,
            )
            for group_index in range(group_count)
            for index, subindex, value in self.module_init_commands
        )
        name_prefix = self.name.rsplit(" ", 1)[0]
        return replace(
            self,
            name=(
                f"{name_prefix} {self.input_channels * group_count}DI/"
                f"{self.output_channels * group_count}DO"
            ),
            rx_bytes=self.rx_bytes * group_count,
            tx_bytes=self.tx_bytes * group_count,
            input_channels=self.input_channels * group_count,
            output_channels=self.output_channels * group_count,
            expected_module_ids=module_ids,
            module_init_commands=module_init_commands,
        )


def is_tolerated_mapping_error(
    error: object,
    profile: RemoteIoProfile,
    slave_position: int,
) -> bool:
    if not isinstance(error, pysoem.SdoError):
        return False
    return (
        error.slave_pos == slave_position
        and (error.index, error.subindex, error.abort_code)
        in profile.tolerated_mapping_sdo_errors
    )


DRIVE_PROFILES = (
    DriveProfile(
        "Leadshine DM3C-EC556",
        0x4321,
        0x8600,
        0x1602,
        0x1A00,
        15,
        19,
        revision=0x0001,
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
        revision=0x0001,
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

WELDING_PROFILES = (
    WeldingProfile(
        "Megmeet 焊机 EtherNet/IP",
        0xE000001B,
        0x00000036,
        0x1600,
        0x1A00,
        37,
        37,
    ),
)

WELDING_PROFILES_BY_ID = {
    (profile.vendor, profile.product): profile for profile in WELDING_PROFILES
}

REMOTE_IO_PROFILES = (
    RemoteIoProfile(
        "HAUTO DIO 16DI/16DO",
        0x00000001,
        0x00010200,
        0x1600,
        0x1A00,
        2,
        2,
        16,
        16,
        allow_partial_wkc=True,
    ),
    RemoteIoProfile(
        "DECOWELL EX-203S + EX-313S 32DI/32DO",
        0x00444543,
        0x00000001,
        0x1601,
        0x1A00,
        4,
        8,
        32,
        32,
        expected_module_ids=(0x7C, 0x7F),
        module_init_commands=(
            (0x8000, 1, b"\x7C\x00"),
            (0x8010, 1, b"\x7F\x00"),
        ),
    ),
    RemoteIoProfile(
        "Solidot EC4-1616A 16DI/16DO",
        0x00884443,
        0x00000004,
        0x1600,
        0x1A00,
        2,
        2,
        16,
        16,
        revision=0x00000001,
        allow_partial_wkc=True,
        tolerated_mapping_sdo_errors=((0x1C00, 0, 0x06020000),),
    ),
    RemoteIoProfile(
        "Solidot XB6S-EC2002 Modular Coupler",
        0x00884443,
        0x00005601,
        0x16FF,
        0x1AFF,
        0,
        0,
        0,
        0,
        revision=0x00000001,
        allow_partial_wkc=True,
        module_config_protocol="f030_array",
        coupler_rx_bytes=2,
        coupler_tx_bytes=2,
    ),
)

REMOTE_IO_PROFILES_BY_ID = {
    (profile.vendor, profile.product): profile for profile in REMOTE_IO_PROFILES
}


def get_drive_profile(
    vendor: int, product: int, revision: int | None = None
) -> DriveProfile | None:
    profile = PROFILES_BY_ID.get((vendor, product))
    if profile is None:
        return None
    if profile.revision is not None and revision is not None and revision != profile.revision:
        return None
    return profile


def get_welding_profile(vendor: int, product: int) -> WeldingProfile | None:
    return WELDING_PROFILES_BY_ID.get((vendor, product))


def get_remote_io_profile(
    vendor: int,
    product: int,
    revision: int | None = None,
) -> RemoteIoProfile | None:
    profile = REMOTE_IO_PROFILES_BY_ID.get((vendor, product))
    if profile is None:
        return None
    if profile.revision is not None and revision is not None and revision != profile.revision:
        return None
    return profile


def read_detected_module_ids(slave: object) -> tuple[int, ...]:
    """Read the non-zero module identifiers reported by a modular coupler."""
    raw_count = slave.sdo_read(0xF050, 0)
    count = int.from_bytes(raw_count, "little")
    if not 0 < count <= 64:
        raise RuntimeError(f"invalid detected module count from 0xF050: {count}")

    module_ids = []
    for subindex in range(1, count + 1):
        raw_module_id = slave.sdo_read(0xF050, subindex)
        module_id = int.from_bytes(raw_module_id, "little")
        if module_id:
            module_ids.append(module_id)
    return tuple(module_ids)


def resolve_remote_io_profile(
    slave: object, profile: RemoteIoProfile
) -> RemoteIoProfile:
    """Detect installed modules and return a profile adjusted to the actual combination."""
    if profile.module_config_protocol == "f030_array":
        return _resolve_xb6_modules(slave, profile)
    if not profile.module_init_commands:
        return profile
    return profile.for_detected_modules(read_detected_module_ids(slave))


# XB6 module ID -> (di_channels, do_channels, rx_bytes, tx_bytes)
_XB6_MODULE_TABLE: dict[int, tuple[int, int, int, int]] = {
    0x00000627: (32, 0, 0, 4),   # XB6-3200B  32DI PNP
    0x00000629: (16, 0, 0, 2),   # XB6-1600B  16DI PNP
    0x00000631: (8,  0, 0, 1),   # XB6-0800B   8DI PNP
    0x00000626: (32, 0, 0, 4),   # XB6-3200A  32DI NPN
    0x000A626:  (32, 0, 0, 4),   # XB6-3200N  32DI NPN
    0x00000628: (16, 0, 0, 2),   # XB6-1600A  16DI NPN
    0x00000630: (8,  0, 0, 1),   # XB6-0800A   8DI NPN
    0x00000623: (0, 32, 4, 0),   # XB6-0032B  32DO PNP
    0x000B623:  (0, 32, 4, 0),   # XB6-0032BN 32DO NPN
    0x00000625: (0, 16, 2, 0),   # XB6-0016B  16DO PNP
    0x00000633: (0,  8, 1, 0),   # XB6-0008B   8DO PNP
    0x00000622: (0, 32, 4, 0),   # XB6-0032A  32DO NPN
    0x000A622:  (0, 32, 4, 0),   # XB6-0032AN 32DO NPN
    0x00000624: (0, 16, 2, 0),   # XB6-0016A  16DO NPN
    0x00000632: (0,  8, 1, 0),   # XB6-0008A   8DO NPN
    0x00000700: (0, 16, 2, 0),   # XB6-VT16   16DO
    0x00000612: (0, 12, 2, 0),   # XB6-0012J  12DO
    0x00000620: (16, 16, 2, 2),  # XB6-1616A  16DI/16DO NPN
    0x00000621: (16, 16, 2, 2),  # XB6-1616B  16DI/16DO PNP
    # XB6S series (newer firmware, module IDs not in original ESI)
    0x0000E10C: (0, 32, 4, 0),   # XB6S-0032  32DO
    0x0000E104: (32, 0, 0, 4),   # XB6S-3200  32DI
}


def _resolve_xb6_modules(
    slave: object, profile: RemoteIoProfile
) -> RemoteIoProfile:
    """Resolve XB6-EC0002 modular coupler using standard ETG.5001 0xF050 detection."""
    module_ids = read_detected_module_ids(slave)
    if not module_ids:
        raise RuntimeError(
            f"no XB6 I/O modules detected on {profile.name}; "
            "insert at least one digital I/O module"
        )
    total_di = 0
    total_do = 0
    total_rx = 0
    total_tx = 0
    for mid in module_ids:
        info = _XB6_MODULE_TABLE.get(mid)
        if info is None:
            known = ", ".join(f"0x{k:08X}" for k in _XB6_MODULE_TABLE)
            raise RuntimeError(
                f"unsupported XB6 module 0x{mid:08X}; "
                f"supported modules: {known}"
            )
        di, do, rx, tx = info
        total_di += di
        total_do += do
        total_rx += rx
        total_tx += tx
    return replace(
        profile,
        expected_module_ids=module_ids,
        rx_bytes=total_rx + profile.coupler_rx_bytes,
        tx_bytes=total_tx + profile.coupler_tx_bytes,
        input_channels=total_di,
        output_channels=total_do,
        name=(
            f"{profile.name} {total_di}DI/{total_do}DO"
            if total_di or total_do
            else profile.name
        ),
    )


def initialize_remote_io_modules(
    slave: object, profile: RemoteIoProfile
) -> RemoteIoProfile:
    """Apply ESI-defined slot identifiers before mapping a modular I/O device."""
    if profile.module_config_protocol == "f030_array":
        return _initialize_xb6_modules(slave, profile)
    if not profile.module_init_commands:
        return profile

    resolved_profile = resolve_remote_io_profile(slave, profile)
    for index, subindex, value in resolved_profile.module_init_commands:
        slave.sdo_write(index, subindex, value)
    return resolved_profile


def _initialize_xb6_modules(
    slave: object, profile: RemoteIoProfile
) -> RemoteIoProfile:
    """Write detected module IDs to 0xF030 Configured Module Ident List."""
    resolved_profile = resolve_remote_io_profile(slave, profile)
    module_ids = resolved_profile.expected_module_ids
    count = len(module_ids)
    # ETG.5001: set SubIndex 0 to 0 first to allow writing elements,
    # then write each module ID, then set SubIndex 0 to the final count.
    slave.sdo_write(0xF030, 0, b"\x00")
    for i, mid in enumerate(module_ids, start=1):
        slave.sdo_write(0xF030, i, mid.to_bytes(4, "little"))
    slave.sdo_write(0xF030, 0, count.to_bytes(1, "little"))
    return resolved_profile