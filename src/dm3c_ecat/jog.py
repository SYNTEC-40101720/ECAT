"""Explicit, bounded CiA 402 Profile Velocity jog for DM3C-EC556."""

from __future__ import annotations

import argparse
import struct
import sys
import time

import pysoem

from .device_profiles import DriveProfile, get_drive_profile, resolve_default_interface
CYCLE_US = 10_000


def output_packet(controlword: int, velocity: int, mode: int = 3) -> bytes:
    return (
        struct.pack("<Hii", controlword, velocity, 1000)
        + struct.pack("<i", 1000)
        + struct.pack("<b", mode)
    )


def statusword_from_overlap_output(slave: object) -> int:
    feedback = bytes(slave.output)
    if len(feedback) < 4:
        return 0
    return int.from_bytes(feedback[2:4], "little")


def wait_status(
    master: pysoem.Master,
    slave: object,
    controlword: int,
    mask: int,
    value: int,
    mode: int = 3,
) -> int:
    last_status = 0
    for _ in range(100):
        slave.output = output_packet(controlword, 0, mode)
        master.send_processdata()
        master.receive_processdata(CYCLE_US)
        last_status = statusword_from_overlap_output(slave)
        if last_status & mask == value:
            return last_status
        time.sleep(CYCLE_US / 1_000_000)
    raise RuntimeError(f"CiA 402 state timeout, statusword=0x{last_status:04X}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded DM3C Profile Velocity jog")
    parser.add_argument("velocity", type=int, help="target velocity in drive units")
    parser.add_argument("seconds", type=float, help="maximum jog duration")
    parser.add_argument("interface", nargs="?")
    parser.add_argument("--confirm-jog", action="store_true", help="required acknowledgement for motion")
    args = parser.parse_args()
    interface = args.interface or resolve_default_interface()
    if not interface:
        parser.error("multiple or no physical EtherCAT adapters found; specify interface")

    if not args.confirm_jog:
        parser.error("motion requires --confirm-jog")
    if args.seconds <= 0 or args.seconds > 10:
        parser.error("seconds must be greater than 0 and no more than 10")
    if args.velocity == 0:
        parser.error("velocity must be non-zero for a jog")

    master = pysoem.Master()
    slave = None
    profile: DriveProfile | None = None
    try:
        master.open(interface)
        if master.config_init() != 1:
            raise RuntimeError("expected exactly one EtherCAT slave")
        slave = master.slaves[0]
        profile = get_drive_profile(slave.man, slave.id)
        if profile is None:
            raise RuntimeError(f"unsupported slave 0x{slave.man:08X}/0x{slave.id:08X}")

        for index, pdo in ((0x1C12, profile.rx_pdo), (0x1C13, profile.tx_pdo)):
            slave.sdo_write(index, 0, b"\x00")
            slave.sdo_write(index, 1, pdo.to_bytes(2, "little"))
            slave.sdo_write(index, 0, b"\x01")
        slave.sdo_write(0x6040, 0, (0x0080).to_bytes(2, "little"))
        slave.sdo_write(0x6060, 0, profile.mode.to_bytes(1, "little", signed=True))

        if master.config_overlap_map() != profile.io_map_bytes:
            raise RuntimeError(
                f"unexpected process image size (expected {profile.io_map_bytes} bytes)"
            )
        if (len(slave.output), len(slave.input)) != (profile.rx_bytes, profile.tx_bytes):
            raise RuntimeError(
                f"unexpected Rx/Tx bytes (expected {profile.rx_bytes}/{profile.tx_bytes})"
            )
        slave.output = output_packet(0, 0, profile.mode)
        master.send_processdata()
        if master.receive_processdata(CYCLE_US) != master.expected_wkc:
            raise RuntimeError("initial process-data WKC mismatch")

        master.state = pysoem.OP_STATE
        master.write_state()
        if master.state_check(pysoem.OP_STATE, 200_000) != pysoem.OP_STATE:
            raise RuntimeError("drive did not reach OP")

        wait_status(master, slave, 0x0006, 0x006F, 0x0021, profile.mode)
        wait_status(master, slave, 0x0007, 0x006F, 0x0023, profile.mode)
        wait_status(master, slave, 0x000F, 0x006F, 0x0027, profile.mode)

        print(f"Jogging velocity={args.velocity} for up to {args.seconds:.2f}s")
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            slave.output = output_packet(0x000F, args.velocity, profile.mode)
            master.send_processdata()
            received = master.receive_processdata(CYCLE_US)
            if received != master.expected_wkc:
                raise RuntimeError(f"process-data WKC mismatch: {received}")
            time.sleep(CYCLE_US / 1_000_000)
        return 0
    except KeyboardInterrupt:
        print("Jog interrupted.", file=sys.stderr)
        return 130
    finally:
        if slave is not None:
            try:
                slave.output = output_packet(0x0006, 0, profile.mode if profile else 3)
                master.send_processdata()
                master.receive_processdata(CYCLE_US)
            except Exception:
                pass
        master.close()


if __name__ == "__main__":
    raise SystemExit(main())
