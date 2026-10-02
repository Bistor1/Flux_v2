"""System tray and desktop notifications. Tray callbacks are not on the Tk thread."""

import subprocess
import threading

try:
    import pystray
    PYSTRAY_AVAILABLE = True
except ImportError:
    pystray = None
    PYSTRAY_AVAILABLE = False


def notify(title, msg):
    """Best-effort desktop notification. Never raises."""
    try:
        subprocess.run(
            ["notify-send", "-a", "Flux v2", title, msg],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        pass


def start_tray(image, on_open, on_quit):
    """Run a pystray icon on a daemon thread.

    on_open and on_quit are invoked on the tray thread. Returns the icon,
    or None when tray support is unavailable.
    """
    if not PYSTRAY_AVAILABLE or image is None:
        return None

    def _open(icon=None):
        on_open()

    def _quit(icon=None):
        try:
            if icon is not None:
                icon.stop()
        except Exception:
            pass
        on_quit()

    icon = pystray.Icon(
        "fluxv2",
        image,
        "Flux v2",
        pystray.Menu(
            pystray.MenuItem("Open", _open, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", _quit),
        ),
    )
    threading.Thread(target=icon.run, daemon=True).start()
    return icon
