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


def run():
    """Own the instance, then restore a leftover tint. A second launch must not."""
    _require_gui()
    from fluxv2.app import FluxApp
    from fluxv2.backend import install_exit_signals, install_restore_hook
    from fluxv2.display import init_x11_threads
    from fluxv2.instance import single_instance_or_signal

    # Before Tk and before any XOpenDisplay. A secondary process may call this
    # too; it does not connect to the display.
    init_x11_threads()
    lock = single_instance_or_signal()
    if lock is None:
        sys.exit(0)
    # Before the restore hook, so a logout during startup sets the flag
    # instead of killing the process mid-restore.
    install_exit_signals()
    install_restore_hook()
    try:
        FluxApp(lock=lock).mainloop()
    finally:
        try:
            lock.close()
        except Exception:
            pass


def main():
    run()


if __name__ == "__main__":
    main()
