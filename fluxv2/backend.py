"""Screen color temperature. The ramps are ours; nothing else is asked to tint the screen."""

import atexit
import os

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
    "install_restore_hook",
    "restore_display",
    "saved_temperature",
    "set_temperature",
]


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
