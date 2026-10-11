"""Command line entry point: ``python -m iconchanger``."""
from __future__ import annotations

import argparse
import sys

from . import APP_ID, APP_NAME, __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_ID, description=f"{APP_NAME} - change the icon of any installed app.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--list", action="store_true", help="list apps with a custom icon and exit")
    parser.add_argument("--sync", action="store_true",
                        help="repair custom icons after apps were updated or removed, then exit")
    parser.add_argument("--reset-all", action="store_true", help="give every app its original icon back and exit")
    args, qt_args = parser.parse_known_args(argv)

    if args.list or args.sync or args.reset_all:
        from .manager import IconManager

        mgr = IconManager(background=False)
        if args.sync:
            for line in mgr.sync():
                print(line)
        if args.reset_all:
            print(f"Restored {mgr.reset_all()} original icon(s)")
        if args.list:
            for cust in sorted(mgr.customizations.values(), key=lambda c: c.desktop_id):
                note = " (app not installed)" if cust.suspended else ""
                print(f"{cust.desktop_id}\t{cust.icon}{note}")
        return 0

    try:
        from .gui import run
    except ImportError as exc:
        print(f"{APP_NAME} needs PyQt6: sudo pacman -S python-pyqt6 qt6-svg\n({exc})", file=sys.stderr)
        return 1
    return run([sys.argv[0], *qt_args])


if __name__ == "__main__":
    sys.exit(main())
