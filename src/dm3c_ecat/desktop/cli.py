"""ecat-desktop 命令入口：单进程启动 FastAPI + WebView2 桌面窗口。

用法::

    ecat-desktop                    # 桌面窗口（默认）
    ecat-desktop --no-window        # 浏览器态（开发/冒烟）
    ecat-desktop --interface "\\Device\\NPF_{GUID}"
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from ..device_profiles import resolve_default_interface
from ..hmi import Runtime, _LogCapture
from ..logging_setup import DEFAULT_LOG_FILE, configure_logging
from . import APP_VERSION
from .gateway import ControlGateway
from .launcher import run_desktop


def _static_dir() -> Path | None:
    # 开发态：webui/dist 在仓库根；打包态：PyInstaller datas 放在 _internal/webui/dist
    candidates = (
        Path(__file__).resolve().parents[3] / "webui" / "dist",
        Path(getattr(__import__("sys"), "_MEIPASS", Path(__file__).resolve()))
        / "webui"
        / "dist",
    )
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="SYNTEC ECAT Test desktop HMI")
    parser.add_argument("--version", action="version", version=APP_VERSION)
    parser.add_argument("--interface", help="EtherCAT adapter (overrides ECAT_INTERFACE)")
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_FILE))
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="headless mode: no WebView2 window, wait for shutdown command",
    )
    args = parser.parse_args()
    configure_logging(args.log_file)
    capture = _LogCapture()
    capture.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.getLogger("ecat_test").addHandler(capture)

    static_dir = _static_dir()
    interface = args.interface or resolve_default_interface()
    runtime = Runtime(interface)
    gateway = ControlGateway(runtime)
    logging.getLogger("ecat_test.desktop").info(
        "ecat-desktop %s starting (window=%s, static=%s)",
        APP_VERSION,
        not args.no_window,
        static_dir,
    )
    return run_desktop(
        gateway,
        static_dir,
        open_window=not args.no_window,
    )


if __name__ == "__main__":
    raise SystemExit(main())