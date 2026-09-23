"""CiA 402 motion mode identifiers and process-data metadata."""

from __future__ import annotations

import enum
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
    # Declared RxPDO field layout (index, subindex, bits, role). When provided,
    # the runtime serializes commands by walking the drive-reported RxPDO layout
    # and writing each named field at its real offset, instead of hard-coded
    # struct.pack. Fields absent from this declaration are zero-filled.
    rx_fields: tuple = ()
    # Declared TxPDO field layout. When provided together with the drive profile's
    # required_feedback_roles, the runtime decodes feedback by role from the
    # drive-reported layout rather than from a static whitelist.
    tx_fields: tuple = ()


class PdoRole(enum.IntEnum):
    """Semantic role of a CiA 402 PDO object entry.

    The role decouples "what a field means" from "where it sits in the PDO",
    so the runtime can find a field by role regardless of firmware layout drift.
    The integer value is the CiA 402 object index the role refers to.
    """

    CONTROLWORD = 0x6040
    TARGET_POSITION = 0x607A
    TARGET_VELOCITY = 0x60FF
    TARGET_TORQUE = 0x6071
    MODE_OF_OPERATION = 0x6060
    DIGITAL_OUTPUT = 0x60FE
    TOUCH_PROBE = 0x60B0
    # Vendor / CiA 402 ramp fields sometimes carried in the RxPDO on stepper
    # drives such as Leadshine DM3C; when absent they are set via SDO instead.
    ACCELERATION = 0x6083
    DECELERATION = 0x6084
    PROFILE_VELOCITY = 0x6081

    STATUSWORD = 0x6041
    ACTUAL_POSITION = 0x6064
    ACTUAL_VELOCITY = 0x606C
    ACTUAL_TORQUE = 0x6077
    TORQUE_ACTUAL = 0x60B9
    VELOCITY_DEMAND = 0x60BA
    MODE_OF_OPERATION_DISPLAY = 0x6061
    ERROR_CODE = 0x603F
    DIGITAL_INPUT = 0x60FD
    FOLLOWING_ERROR = 0x60F4

    @property
    def index(self) -> int:
        return int(self)


_SIGNED_ROLES = frozenset(
    {
        PdoRole.TARGET_POSITION,
        PdoRole.TARGET_VELOCITY,
        PdoRole.TARGET_TORQUE,
        PdoRole.MODE_OF_OPERATION,
        PdoRole.ACTUAL_POSITION,
        PdoRole.ACTUAL_VELOCITY,
        PdoRole.ACTUAL_TORQUE,
        PdoRole.TORQUE_ACTUAL,
        PdoRole.VELOCITY_DEMAND,
        PdoRole.MODE_OF_OPERATION_DISPLAY,
        PdoRole.FOLLOWING_ERROR,
        PdoRole.ACCELERATION,
        PdoRole.DECELERATION,
        PdoRole.PROFILE_VELOCITY,
    }
)


@dataclass(frozen=True, slots=True)
class PdoField:
    """A declared PDO entry and the role it plays for a motion mode.

    ``index``/``subindex`` identify the CiA 402 object; ``role`` tells the runtime
    how to interpret the value when serializing commands or decoding feedback.
    """

    index: int
    subindex: int
    bits: int
    role: PdoRole | None = None

    @property
    def key(self) -> tuple[int, int]:
        return (self.index, self.subindex)

    @property
    def signed(self) -> bool:
        return self.role in _SIGNED_ROLES
