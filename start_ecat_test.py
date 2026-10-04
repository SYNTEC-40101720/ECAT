"""Start the SYNTEC ECAT Test desktop HMI.

Thin wrapper: prefers the packaged backend entry (dm3c_ecat.desktop.cli) when
the package is importable; falls back to running the module from the repo so
the launcher works in a source checkout without an install step.

Usage:
    py start_ecat_test.py [--interface \\Device\\NPF_{GUID}] [--no-window]
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def main() -> int:
    args = [sys.executable, "-m", "dm3c_ecat.desktop.cli", *sys.argv[1:]]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    try:
        return subprocess.run(args, cwd=PROJECT_ROOT, env=env, check=False).returncode
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
