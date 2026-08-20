"""CiA 402 motion mode identifiers and process-data metadata."""

from __future__ import annotations

from dataclasses import dataclass

MODE_PP = "pp"
MODE_VM = "vm"
MODE_PV = "pv"
MODE_PT = "pt"
MODE_HM = "hm"
MODE_IP = "ip"
MODE_CSP = "csp"
MODE_CSV = "csv"
MODE_CST = "cst"

MOTION_MODES = (
    MODE_PP,
    MODE_VM,
    MODE_PV,
    MODE_PT,
    MODE_HM,
    MODE_IP,
    MODE_CSP,
    MODE_CSV,
    MODE_CST,
)

MODE_VALUES = {
    MODE_PP: 1,
    MODE_VM: 2,
    MODE_PV: 3,
    MODE_PT: 4,
    MODE_HM: 6,
    MODE_IP: 7,
    MODE_CSP: 8,
    MODE_CSV: 9,
    MODE_CST: 10,
}

MODE_CAPABILITY_BITS = {
    MODE_PP: 1 << 0,
    MODE_VM: 1 << 1,
    MODE_PV: 1 << 2,
    MODE_PT: 1 << 3,
    MODE_HM: 1 << 4,
    MODE_IP: 1 << 5,
    MODE_CSP: 1 << 6,
    MODE_CSV: 1 << 7,
    MODE_CST: 1 << 8,
}


def modes_from_capability_word(
    capability_word: int, candidates: tuple[str, ...]
) -> tuple[str, ...]:
    return tuple(
        mode
        for mode in candidates
        if capability_word & MODE_CAPABILITY_BITS[mode]
    )


@dataclass(frozen=True, slots=True)
class ModePdo:
    mode: str
    rx_pdo: int
    rx_bytes: int
    packet_kind: str
    mode_in_pdo: bool = True
    target_velocity_in_pdo: bool = False
