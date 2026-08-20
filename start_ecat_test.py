from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def find_npm() -> str | None:
    candidates = ("npm.cmd", "npm") if os.name == "nt" else ("npm",)
    for candidate in candidates:
        executable = shutil.which(candidate)
        if executable:
            return executable
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start the ECAT Test Electron HMI and Python WebSocket backend."
    )
    parser.add_argument(
        "--interface",
        help="EtherCAT adapter name; overrides the ECAT_INTERFACE environment variable.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    npm = find_npm()
    if npm is None:
        print("[ERROR] npm was not found. Please install Node.js first.")
        return 1

    environment = os.environ.copy()
    if args.interface:
        environment["ECAT_INTERFACE"] = args.interface

    print("Starting ECAT Test Electron HMI...")
    try:
        completed = subprocess.run(
            [npm, "start"],
            cwd=PROJECT_ROOT,
            env=environment,
            check=False,
        )
    except KeyboardInterrupt:
        return 130
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
