#!/usr/bin/env python3
"""Flux v2 entry point. The window lives in fluxv2.app."""

import sys


def _require_gui():
    try:
        import customtkinter  # noqa: F401
    except ImportError:
        print("ERROR: customtkinter is not installed.", file=sys.stderr)
        print("  pip install customtkinter", file=sys.stderr)
        sys.exit(1)
    try:
        from tkinter import messagebox  # noqa: F401
    except ImportError:
        print("ERROR: tkinter is not installed.", file=sys.stderr)
        print("  sudo apt install python3-tk", file=sys.stderr)
        sys.exit(1)


def main():
    _require_gui()
    from fluxv2.app import FluxApp
    from fluxv2.backend import install_restore_hook
    from fluxv2.instance import single_instance_or_signal

    install_restore_hook()

    lock = single_instance_or_signal()
    if lock is None:
        sys.exit(0)
    try:
        FluxApp(lock=lock).mainloop()
    finally:
        try:
            lock.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
