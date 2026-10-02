"""Color-temperature backends: KWin Night Light, redshift, or gammastep."""

import atexit
import json
import os
import shutil
import subprocess
from pathlib import Path

KWIN_SERVICE = "org.kde.KWin"
KWIN_PATH = "/org/kde/KWin/NightLight"
KWIN_IFACE = "org.kde.KWin.NightLight"
# Keys Flux writes. Anything else in the group is left alone.
_MANAGED_KEYS = ("Active", "Mode", "NightTemperature")


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


def kwin_stop_preview():
    """Drop a running preview. preview() is what shows the OSD banner."""
    result = _busctl([
        "call", KWIN_SERVICE, KWIN_PATH, KWIN_IFACE, "stopPreview",
    ])
    if result is None:
        return False, "busctl is not available"
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "KWin stopPreview failed").strip()
        return False, err
    return True, ""


def _kwinrc():
    return Path.home() / ".config" / "kwinrc"


def _snapshot_path():
    state = os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
    directory = Path(state) / "fluxv2"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "kwin-nightcolor.json"


def _read_nightcolor():
    path = _kwinrc()
    if not path.is_file():
        return {}
    section = {}
    in_group = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_group:
                break
            in_group = stripped == "[NightColor]"
            continue
        if in_group and "=" in line and not stripped.startswith(("#", ";")):
            key, value = line.split("=", 1)
            section[key.strip()] = value.strip()
    return section


def _kwrite(key, value=None, delete=False, notify=False):
    if not shutil.which("kwriteconfig6"):
        return False, "kwriteconfig6 is not available"
    cmd = ["kwriteconfig6", "--file", "kwinrc", "--group", "NightColor", "--key", key]
    if notify:
        cmd.append("--notify")
    if delete:
        cmd.append("--delete")
    else:
        if key == "Active":
            cmd.extend(["--type", "bool"])
        cmd.append(str(value))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, str(exc)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "kwriteconfig6 failed").strip()
        return False, err
    return True, ""


def _remember_original():
    path = _snapshot_path()
    if path.is_file():
        return True, ""
    path.write_text(json.dumps(_read_nightcolor()), encoding="utf-8")
    return True, ""


def kwin_set_temperature(temp):
    """Apply a constant Night Light temperature without the preview OSD.

    preview() is what makes Plasma show "Farbtemperaturvorschau". Writing
    the NightColor config and notifying KWin does not.
    """
    ok, err = _remember_original()
    if not ok:
        return ok, err
    writes = (
        ("Active", "true"),
        ("Mode", "Constant"),
        ("NightTemperature", str(int(temp))),
    )
    for index, (key, value) in enumerate(writes):
        ok, err = _kwrite(key, value, notify=(index == len(writes) - 1))
        if not ok:
            return ok, err
    return True, ""


def kwin_release():
    """Put Night Light back to how it was before Flux changed it."""
    kwin_stop_preview()
    path = _snapshot_path()
    if not path.is_file():
        return True, ""
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    ops = []
    for key in _MANAGED_KEYS:
        if key in saved:
            ops.append((key, saved[key], False))
        else:
            ops.append((key, None, True))
    if not ops:
        path.unlink(missing_ok=True)
        return True, ""
    for index, (key, value, delete) in enumerate(ops):
        ok, err = _kwrite(key, value, delete=delete, notify=(index == len(ops) - 1))
        if not ok:
            return ok, err
    path.unlink(missing_ok=True)
    return True, ""


def install_kwin_restore_hook():
    """Restore Night Light on Ctrl+C and normal interpreter shutdown."""
    atexit.register(kwin_release)


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
