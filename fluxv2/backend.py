"""Color-temperature backends: KWin Night Light, redshift, or gammastep."""

import shutil
import subprocess

# preview() lasts ~15s. A repeat of the same value does not restart that
# timer, so the holder nudges by 1 K well before it expires.
KWIN_PREVIEW_REFRESH_MS = 8000
KWIN_SERVICE = "org.kde.KWin"
KWIN_PATH = "/org/kde/KWin/NightLight"
KWIN_IFACE = "org.kde.KWin.NightLight"


def _busctl(args, timeout=3):
    """Call busctl --user. Returns CompletedProcess, or None if it cannot run."""
    if not shutil.which("busctl"):
        return None
    try:
        return subprocess.run(
            ["busctl", "--user", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None


def kwin_nightlight_available():
    """True when KWin exposes Night Light. Redshift cannot drive KWin."""
    result = _busctl([
        "get-property", KWIN_SERVICE, KWIN_PATH, KWIN_IFACE, "available",
    ])
    if result is None or result.returncode != 0:
        return False
    return "true" in (result.stdout or "").lower()


def kwin_preview(temp):
    """Apply a one-shot color temperature via KWin Night Light."""
    result = _busctl([
        "call", KWIN_SERVICE, KWIN_PATH, KWIN_IFACE,
        "preview", "u", str(int(temp)),
    ])
    if result is None:
        return False, "busctl is not available"
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "KWin Night Light call failed").strip()
        return False, err
    return True, ""


def kwin_stop_preview():
    """Drop the Night Light preview and return the screen to normal."""
    result = _busctl([
        "call", KWIN_SERVICE, KWIN_PATH, KWIN_IFACE, "stopPreview",
    ])
    if result is None:
        return False, "busctl is not available"
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "KWin stopPreview failed").strip()
        return False, err
    return True, ""


def keepalive_temp(hold, last_sent, min_temp):
    """Temperature to send so a repeated KWin preview restarts its timer."""
    alt = hold - 1 if hold > min_temp else hold + 1
    return alt if last_sent == hold else hold


def detect_color_backend():
    """KWin on KDE, otherwise redshift, otherwise gammastep."""
    if kwin_nightlight_available():
        return "kwin"
    if shutil.which("redshift"):
        return "redshift"
    if shutil.which("gammastep"):
        return "gammastep"
    return None


def run_cli(binary, temp=None, reset=False, disable=False):
    """One-shot redshift/gammastep call. reset and disable both use -x."""
    try:
        if reset or disable:
            cmd = [binary, "-x"]
        else:
            cmd = [binary, "-P", "-O", str(temp)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return True, ""
        return False, result.stderr.strip() or "Unknown error"
    except subprocess.TimeoutExpired:
        return False, f"{binary} timed out"
    except FileNotFoundError:
        return False, f"{binary} is not installed"
    except Exception as exc:
        return False, str(exc)
