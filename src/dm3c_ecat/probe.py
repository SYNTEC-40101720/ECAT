"""Read-only EtherCAT discovery for supported EtherCAT devices."""

from __future__ import annotations

import argparse
import sys

import pysoem

from .device_profiles import (
    DriveProfile,
    RemoteIoProfile,
    get_drive_profile,
    get_remote_io_profile,
    get_welding_profile,
    initialize_remote_io_modules,
    is_tolerated_mapping_error,
    resolve_remote_io_profile,
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


def ensure_preop(master: object) -> None:
    master.read_state()
    master.state = pysoem.PREOP_STATE
    master.write_state()
    reached = master.state_check(pysoem.PREOP_STATE, 200_000)
    if reached != pysoem.PREOP_STATE:
        raise RuntimeError(f"slave(s) did not reach PRE-OP: 0x{reached:04X}")


def map_process_data(
    master: pysoem.Master,
    *,
    overlap: bool,
    io_entries: list[tuple[int, object, RemoteIoProfile]],
) -> int:
    mapper = master.config_overlap_map if overlap else master.config_map
    try:
        return mapper()
    except pysoem.ConfigMapError as exc:
        errors = getattr(exc, "error_list", ())
        for index, _slave, profile in io_entries:
            if errors and all(
                is_tolerated_mapping_error(error, profile, index)
                for error in errors
            ):
                mapped_size = sum(
                    len(candidate.output) + len(candidate.input)
                    for candidate in master.slaves
                )
                print(
                    f"WARNING: ignoring known fixed-PDO mapping SDO error for "
                    f"{profile.name} at slave {index}; mapped process image is "
                    f"{mapped_size} bytes."
                )
                return mapped_size
        raise


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
    parser.add_argument(
        "--initialize-modular-io",
        action="store_true",
        help="write ESI-defined module IDs for a supported modular I/O coupler",
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
        io_entries = []
        for index, slave in enumerate(master.slaves, start=1):
            profile = get_remote_io_profile(
                slave.man,
                slave.id,
                get_int(slave, "rev"),
            )
            if profile is not None:
                io_entries.append((index, slave, profile))
        welding_entries = [
            (index, slave, profile)
            for index, slave in enumerate(master.slaves, start=1)
            if (profile := get_welding_profile(slave.man, slave.id)) is not None
        ]
        modular_io_entries = [
            (index, slave, profile)
            for index, slave, profile in io_entries
            if profile is not None and profile.module_init_commands
        ]
        resolved_io_profiles = {
            index: profile for index, _slave, profile in io_entries
        }
        if modular_io_entries:
            ensure_preop(master)
            for index, slave, profile in modular_io_entries:
                resolved_profile = (
                    initialize_remote_io_modules(slave, profile)
                    if args.initialize_modular_io
                    else resolve_remote_io_profile(slave, profile)
                )
                resolved_io_profiles[index] = resolved_profile
                if args.initialize_modular_io:
                    print(f"Initialized modular I/O modules: {resolved_profile.name}")
        if modular_io_entries and not args.initialize_modular_io:
            print(
                "WARNING: modular I/O requires --initialize-modular-io before "
                "process-data testing."
            )
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

        use_overlap_map = args.configure_velocity_pdo or bool(
            welding_entries and (drive_entries or io_entries)
        )
        io_map_size = map_process_data(
            master,
            overlap=use_overlap_map,
            io_entries=io_entries,
        )

        if args.configure_velocity_pdo:
            master.state = pysoem.SAFEOP_STATE
            master.write_state()
            reached_state = master.state_check(pysoem.SAFEOP_STATE, 50_000)
            print(f"Requested SAFE-OP state result: 0x{reached_state:04X}")
        elif args.cycle_once:
            master.state = pysoem.SAFEOP_STATE
            master.write_state()
            reached_state = master.state_check(pysoem.SAFEOP_STATE, 50_000)
            print(f"Requested SAFE-OP state result: 0x{reached_state:04X}")
            if reached_state != pysoem.SAFEOP_STATE:
                raise RuntimeError("slave(s) did not reach SAFE-OP")

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
            io_profile = get_remote_io_profile(vendor, product, revision)
            welding_profile = get_welding_profile(vendor, product)
            if io_profile is not None:
                resolved_profile = resolved_io_profiles.get(index, io_profile)
                print(
                    f"  Profile: {resolved_profile.input_channels}DI/"
                    f"{resolved_profile.output_channels}DO RxPDO 0x{resolved_profile.rx_pdo:04X} "
                    f"/ TxPDO 0x{resolved_profile.tx_pdo:04X}"
                )
                assigned_rx_bits[index] = resolved_profile.rx_bytes * 8
            elif drive_profile is not None:
                print(f"  0x1C12:00: {read_sdo_hex(slave, 0x1C12, 0):s}")
                print(f"  0x1C12:01: {read_sdo_hex(slave, 0x1C12, 1):s}")
                print(f"  0x1C13:00: {read_sdo_hex(slave, 0x1C13, 0):s}")
                print(f"  0x1C13:01: {read_sdo_hex(slave, 0x1C13, 1):s}")
                rx_pdo = drive_profile.rx_pdo if args.configure_velocity_pdo else 0x1602
                assigned_rx_bits[index] = print_mapping(slave, rx_pdo, "RxPDO")
                print_mapping(slave, drive_profile.tx_pdo, "TxPDO")
            elif welding_profile is not None:
                print(
                    f"  Profile: {welding_profile.name} RxPDO 0x{welding_profile.rx_pdo:04X} "
                    f"/ TxPDO 0x{welding_profile.tx_pdo:04X} "
                    f"Rx/Tx {welding_profile.rx_bytes}/{welding_profile.tx_bytes} bytes "
                    f"(command/status {welding_profile.command_bytes}/"
                    f"{welding_profile.status_bytes} bytes)"
                )
                assigned_rx_bits[index] = welding_profile.rx_bytes * 8
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
            supported_profile = supported_profile or resolved_io_profiles.get(index)
            supported_profile = supported_profile or get_remote_io_profile(
                slave.man,
                slave.id,
                revision,
            )
            supported_profile = supported_profile or get_welding_profile(slave.man, slave.id)
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
            if use_overlap_map:
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
            print(
                "Probe complete; modular I/O initialization may write ESI slot IDs, "
                "but no OP request or motion command was sent."
            )
        return 0
    except Exception as exc:
        print(f"Probe failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        master.close()


if __name__ == "__main__":
    raise SystemExit(main())
