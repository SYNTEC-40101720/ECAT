"""``py -m dm3c_ecat`` entry point: forward to the desktop CLI."""

from dm3c_ecat.desktop.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
