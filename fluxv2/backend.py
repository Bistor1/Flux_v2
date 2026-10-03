"""Screen color temperature. The ramps are ours; nothing else is asked to tint the screen."""

import atexit

from fluxv2.display import (
    color_control_available,
    restore_display,
    set_temperature,
)


def apply_temperature(temp=None, reset=False, disable=False):
    """Apply a temperature, or put the screen back. Returns (ok, error)."""
    if reset or disable or temp is None:
        return restore_display()
    return set_temperature(temp)


def install_restore_hook():
    """Clear a leftover tint, and restore again when the process exits."""
    restore_display()
    atexit.register(restore_display)
