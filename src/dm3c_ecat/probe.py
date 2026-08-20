"""Read-only EtherCAT discovery for supported EtherCAT devices."""

from __future__ import annotations

import argparse
import sys

import pysoem

from .device_profiles import (
    DriveProfile,
    get_drive_profile,
    get_remote_io_profile,
    resolve_default_interface,
)


def get_int(obj: object, name: str, default: int = 0) -> int:
    value = getattr(obj, name, default)
    return int(value) if value is not None else default


def read_sdo_hex(slave: object, index: int, subindex: int) -> str:
    try:
        value = slave.sdo_read(index, subindex)
        return value.hex()
    except Exception as exc:
        return f"unavailable ({type(exc).__name__})"


def print_mapping(slave: object, index: int, label: str) -> int:
    count_hex = read_sdo_hex(slave, index, 0)
    print(f"  {label} 0x{index:04X}:00: {count_hex}")
    try:
        count = int.from_bytes(slave.sdo_read(index, 0), "little")
    except Exception:
        return 0

    total_bits = 0
    for subindex in range(1, count + 1):
        value = slave.sdo_read(index, subindex)
        print(f"  {label} 0x{index:04X}:{subindex:02X}: {value.hex()}")
        total_bits += int.from_bytes(value, "little") & 0xFF
    return total_bits


def configure_velocity_pdo(slave: object, profile: DriveProfile) -> None:
    slave.sdo_write(0x1C12, 0, b"\x00")
    slave.sdo_write(0x1C12, 1, profile.rx_pdo.to_bytes(2, "little"))
    slave.sdo_write(0x1C12, 0, b"\x01")
    slave.sdo_write(0x1C13, 0, b"\x00")
    slave.sdo_write(0x1C13, 1, profile.tx_pdo.to_bytes(2, "little"))
    slave.sdo_write(0x1C13, 0, b"\x01")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only pysoem DM3C probe")
    parser.add_argument("interface", nargs="?")
    parser.add_argument(
        "--cycle-once",
        action="store_true",
        help="send one zero-output SAFE-OP process-data cycle and print WKC",
    )
    parser.add_argument(
        "--configure-velocity-pdo",
        action="store_true",
        help="write 0x1C12=0x1602 and request SAFE-OP; no motion or OP",
    )
    args = parser.parse_args()
    interface = args.interface or resolve_default_interface()
    if not interface:
        parser.error("multiple or no physical EtherCAT adapters found; specify interface")

    master = pysoem.Master()
    try:
        print(f"Opening: {interface}")
        master.open(interface)
        count = master.config_init()
        print(f"Slaves: {count}")
        if count <= 0:
            print("No EtherCAT slaves found.", file=sys.stderr)
            return 2

        drive_entries = [
            (slave, get_drive_profile(slave.man, slave.id))
            for slave in master.slaves
            if get_drive_profile(slave.man, slave.id) is not None
        ]
        if args.configure_velocity_pdo:
            if not drive_entries:
                raise RuntimeError(
                    "velocity PDO configuration requires a supported drive"
                )
            drive_slave, drive_profile = drive_entries[0]
            print(
                f"Configuring drive 0x1C12 -> 0x{drive_profile.rx_pdo:04X}, "
                f"0x1C13 -> 0x{drive_profile.tx_pdo:04X}"
            )
            configure_velocity_pdo(drive_slave, drive_profile)

        io_map_size = (
            master.config_overlap_map()
            if args.configure_velocity_pdo
            else master.config_map()
        )

        if args.configure_velocity_pdo:
            master.state = pysoem.SAFEOP_STATE
            master.write_state()
            reached_state = master.state_check(pysoem.SAFEOP_STATE, 50_000)
            print(f"Requested SAFE-OP state result: 0x{reached_state:04X}")

        master.read_state()

        assigned_rx_bits: dict[int, int] = {}
        for index, slave in enumerate(master.slaves, start=1):
            vendor = get_int(slave, "man")
            product = get_int(slave, "id")
            revision = get_int(slave, "rev")
            state = get_int(slave, "state")
            al_status = get_int(slave, "al_status")
            al_code = get_int(slave, "al_status_code")
            output_size = len(getattr(slave, "output", b"")) * 8
            input_size = len(getattr(slave, "input", b"")) * 8

            print(f"Slave {index}: {getattr(slave, 'name', '')}")
            print(f"  vendor : 0x{vendor:08X}")
            print(f"  product: 0x{product:08X}")
            print(f"  rev    : 0x{revision:08X}")
            print(f"  state  : 0x{state:04X}")
            print(f"  AL     : 0x{al_status:04X}, code 0x{al_code:04X}")
            print(f"  PDO    : Rx {output_size} bits, Tx {input_size} bits")

            drive_profile = get_drive_profile(vendor, product)
            io_profile = get_remote_io_profile(vendor, product)
            if io_profile is not None:
                print(
                    f"  Profile: fixed {io_profile.input_channels}DI/"
                    f"{io_profile.output_channels}DO RxPDO 0x{io_profile.rx_pdo:04X} "
                    f"/ TxPDO 0x{io_profile.tx_pdo:04X}"
                )
                assigned_rx_bits[index] = io_profile.rx_bytes * 8
            elif drive_profile is not None:
                print(f"  0x1C12:00: {read_sdo_hex(slave, 0x1C12, 0):s}")
                print(f"  0x1C12:01: {read_sdo_hex(slave, 0x1C12, 1):s}")
                print(f"  0x1C13:00: {read_sdo_hex(slave, 0x1C13, 0):s}")
                print(f"  0x1C13:01: {read_sdo_hex(slave, 0x1C13, 1):s}")
                rx_pdo = drive_profile.rx_pdo if args.configure_velocity_pdo else 0x1602
                assigned_rx_bits[index] = print_mapping(slave, rx_pdo, "RxPDO")
                print_mapping(slave, drive_profile.tx_pdo, "TxPDO")
            else:
                print("  WARNING: slave is not a supported device profile.")

        print(f"Expected WKC: {getattr(master, 'expected_wkc', 0)}")
        print(f"IO map size: {io_map_size} bytes")
        for index, slave in enumerate(master.slaves, start=1):
            if index in assigned_rx_bits and len(slave.output) * 8 != assigned_rx_bits[index]:
                print(
                    f"WARNING: slave {index} output buffer size differs from the "
                    "assigned PDO layout; do not use this probe for motion commands."
                )
            supported_profile = get_drive_profile(slave.man, slave.id)
            supported_profile = supported_profile or get_remote_io_profile(slave.man, slave.id)
            if supported_profile is not None and (
                len(slave.output) != supported_profile.rx_bytes
                or len(slave.input) != supported_profile.tx_bytes
            ):
                print(
                    f"WARNING: slave {index} process image size differs from the "
                    "supported profile; do not use this probe for motion commands."
                )
        if args.cycle_once:
            for slave in master.slaves:
                slave.output = bytes(len(slave.output))
            if args.configure_velocity_pdo:
                master.send_overlap_processdata()
            else:
                master.send_processdata()
            wkc = master.receive_processdata(10_000)
            print(f"SAFE-OP zero-output WKC: {wkc}")
            for index, slave in enumerate(master.slaves, start=1):
                print(f"  slave {index} output: {bytes(slave.output).hex()}")
                print(f"  slave {index} input : {bytes(slave.input).hex()}")
        if args.configure_velocity_pdo:
            print("PDO configuration complete; no OP request, drive enable, or motion command was sent.")
        else:
            print("Read-only probe complete; no SDO writes or motion commands were sent.")
        return 0
    except Exception as exc:
        print(f"Probe failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        master.close()


if __name__ == "__main__":
    raise SystemExit(main())
