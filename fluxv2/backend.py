"""Screen color temperature. The ramps are ours; nothing else is asked to tint the screen."""

import atexit
import os
import signal

from fluxv2.display import (
    consume_note,
    describe_control,
    display_lock,
    restore_display,
    saved_temperature,
    set_temperature,
)

__all__ = [
    "consume_note",
    "describe_control",
    "display_lock",
    "exit_requested",
    "install_exit_signals",
    "install_restore_hook",
    "restore_display",
    "saved_temperature",
    "set_temperature",
]


_exit_requested = False
_signals_installed = False


def install_exit_signals():
    """On SIGTERM and SIGHUP, set a flag. Do not touch the display here."""
    global _signals_installed
    if _signals_installed:
        return
    _signals_installed = True

    def _handler(_signum, _frame):
        global _exit_requested
        _exit_requested = True

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGHUP, _handler)


def exit_requested():
    """True after SIGTERM or SIGHUP."""
    return _exit_requested


def install_restore_hook():
    """Undo a tint a dead process left behind, and restore again on exit.

    An update restart sets FLUXV2_RESUME so the new process can reapply the
    saved temperature instead of clearing it. FLUXV2_KEEP_TINT stops the
    process that is exiting from clearing that state first.
    """
    atexit.register(_restore_on_exit)
    if os.environ.get("FLUXV2_RESUME") == "1":
        return
    restore_display()


def _restore_on_exit():
    if os.environ.get("FLUXV2_KEEP_TINT") == "1":
        return
    restore_display()
