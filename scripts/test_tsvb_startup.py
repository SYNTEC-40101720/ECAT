"""One-shot startup validation for the Jiutong TSVB-EA drive profile.

Opens the EtherCAT master via the HMI Runtime, configures PV mode, requests
OP, runs a few zero-output cycles, prints feedback, and exits. No motion is
commanded - this only validates that the PDO layout and feedback mapping
match the actual drive.
"""

from __future__ import annotations

import time

from dm3c_ecat.device_profiles import resolve_default_interface
from dm3c_ecat.hmi import Runtime


def main() -> int:
    interface = resolve_default_interface()
    if not interface:
        print("[ERROR] no physical EtherCAT adapter found")
        return 2
    print(f"[INFO] using interface: {interface}")

    runtime = Runtime(interface)
    try:
        runtime.master.open(interface)
        runtime._master_open = True
        print(f"[INFO] EtherCAT interface opened: {interface}")
        runtime.configure()
        snapshot = runtime.snapshot()
        print(f"[OK] device: {snapshot['device']}")
        print(f"[OK] state: {snapshot['state']} - {snapshot['message']}")
        print(f"[OK] motion mode: {snapshot['motionMode']}")
        print(f"[OK] available modes: {snapshot['availableModes']}")
        print(f"[OK] mode capabilities: {snapshot['modeCapabilities']}")
        print(f"[OK] wkc: {snapshot['wkc']}/{snapshot['expectedWkc']}")

        # Run a few zero-output cycles and read feedback.
        for i in range(5):
            runtime.wkc = runtime.cycle(0x0006, 0)
            runtime.feedback()
            time.sleep(0.01)
        print(
            f"[OK] after 5 cycles: statusword={snapshot['statusword']} "
            f"error={snapshot['errorCode']} mode={runtime.mode} "
            f"position={runtime.actual_position} wkc={runtime.wkc}"
        )
        print("[OK] TSVB-EA startup validation passed")
        return 0
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        return 1
    finally:
        runtime.stop()
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
