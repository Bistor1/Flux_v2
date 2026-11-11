"""System tray and desktop notifications. Tray callbacks are not on the Tk thread."""

import os
import subprocess
import sys
import threading

try:
    import pystray

    # pystray resolves its backend while this module is imported, so a failure
    # here means no usable backend at all. Its X11 backend also connects to
    # DISPLAY and raises DisplayNameError, not ImportError, when the socket is
    # missing. __init__ deletes the backend() helper once Icon is bound, so the
    # winner is read back off the Icon class.
    _backend_name = pystray.Icon.__module__
    if _backend_name.endswith("_dummy"):
        pystray = None
        PYSTRAY_AVAILABLE = False
        TRAY_BACKEND_NAME = _backend_name
        _IMPORT_ERROR = "pystray found only its dummy backend"
    else:
        PYSTRAY_AVAILABLE = True
        TRAY_BACKEND_NAME = _backend_name
        _IMPORT_ERROR = ""
except Exception as exc:
    pystray = None
    PYSTRAY_AVAILABLE = False
    TRAY_BACKEND_NAME = ""
    _IMPORT_ERROR = str(exc)


def _tray_class():
    if not PYSTRAY_AVAILABLE:
        return None
    try:
        module = __import__(TRAY_BACKEND_NAME, fromlist=["Icon"])
    except Exception:
        return None
    return getattr(module, "Icon", None)


def tray_usable():
    """Whether a tray icon can plausibly appear on this session.

    A backend that imports is not enough. pystray speaks the XEmbed tray
    protocol and the AppIndicator/GTK status items only; neither reaches a
    Wayland session, whose hosts implement StatusNotifierItem instead, which
    pystray does not implement at all.
    """
    if not PYSTRAY_AVAILABLE:
        return False
    if os.environ.get("PYSTRAY_BACKEND") == "dummy":
        return False
    if sys.platform in ("darwin", "win32"):
        return True
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return False
    if TRAY_BACKEND_NAME.endswith("_xorg"):
        # XEmbed needs an X socket *and* a tray manager to dock into. Under
        # XWayland DISPLAY is set, so the socket alone proves nothing.
        return bool(os.environ.get("DISPLAY"))
    return True


def tray_reason():
    """Human-readable reason the tray is unavailable. Empty when usable."""
    if tray_usable():
        return ""
    if not PYSTRAY_AVAILABLE:
        return _IMPORT_ERROR or "pystray is not installed"
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return (
            f"the tray backend ({TRAY_BACKEND_NAME}) cannot reach a Wayland session; "
            "KDE shows StatusNotifierItem icons, which pystray does not "
            "implement"
        )
    if not os.environ.get("DISPLAY"):
        return "no X11 DISPLAY is set"
    return f"{TRAY_BACKEND_NAME} offers no usable tray"


def menu_is_rendered():
    """Whether the tray backend draws the right-click menu.

    The X11 backend supports only the default action, so a Quit entry added to
    its menu can never be reached and the app needs another way to quit.
    """
    icon_cls = _tray_class()
    if icon_cls is None:
        return False
    return bool(getattr(icon_cls, "HAS_MENU", True))


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

    docked = threading.Event()
    running = threading.Event()
    failed = []

    def _runner():
        try:
            # Set before run() so the watchdog can tell "still starting up"
            # from "already finished", which is the only safe thing to stop on.
            running.set()
            icon.run(setup=lambda i: docked.set())
        except BaseException as exc:
            failed.append(exc)
        finally:
            docked.set()

    def _watch():
        # The default setup thread pystray spawns is where a failed dock
        # surfaces, and it dies before run() returns. This waits on our own
        # event instead, so the verdict cannot be lost to that race.
        #
        # A slow dock is not a failure. Waiting on `docked` alone stopped
        # healthy trays whose manager took longer than the timeout to hand
        # back an XEmbed socket, so stop only once run() has really ended.
        if not running.wait(5.0):
            return
        thread.join()
        if failed:
            try:
                icon.stop()
            except Exception:
                pass

    thread = threading.Thread(target=_runner, daemon=True, name="fluxv2-tray")
    thread.start()
    threading.Thread(target=_watch, daemon=True).start()
    return _TrayHandle(icon, thread, docked, failed)


class _TrayHandle:
    """Stands in for the pystray icon and knows whether it ever came up."""

    def __init__(self, icon, thread, docked, failed):
        self._icon = icon
        self._thread = thread
        self._docked = docked
        self._failed = failed

    @property
    def error(self):
        """The exception that stopped the tray loop, None while it is alive."""
        if not self._docked.wait(5.0) and self._thread.is_alive():
            return None
        if self._failed:
            return self._failed[0]
        if not self._thread.is_alive():
            return RuntimeError("the tray loop ended without an error")
        return None

    def stop(self):
        try:
            self._icon.stop()
        except Exception:
            pass
