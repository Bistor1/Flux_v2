#!/usr/bin/env python3
"""Flux v2 entry point. The window lives in fluxv2.app."""

import os
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
    from fluxv2.instance import lock_socket_path, single_instance_or_signal

    lock = single_instance_or_signal()
    if lock is None:
        sys.exit(0)
    try:
        FluxApp(lock_socket=lock).mainloop()
    finally:
        try:
            lock.close()
        except Exception:
            pass
        try:
            os.unlink(lock_socket_path())
        except OSError:
            pass


if __name__ == "__main__":
    main()
