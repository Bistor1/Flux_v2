"""Color-temperature backends: KWin Night Light, redshift, or gammastep."""

import atexit
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

KWIN_SERVICE = "org.kde.KWin"
KWIN_PATH = "/org/kde/KWin/NightLight"
KWIN_IFACE = "org.kde.KWin.NightLight"
# Keys Flux writes. Anything else in the group is left alone.
_MANAGED_KEYS = ("Active", "Mode", "NightTemperature")


def _in_flatpak():
    return bool(os.environ.get("FLATPAK_ID"))


def _host_argv(args):
    """Host tools are outside the sandbox. flatpak-spawn reaches them."""
    if _in_flatpak() and shutil.which("flatpak-spawn"):
        return ["flatpak-spawn", "--host", *args]
    return list(args)


def _run(args, timeout=5):
    """Run a command, on the host when inside Flatpak.

    Returns CompletedProcess, or None when the program cannot be started.
    """
    if not _in_flatpak() and shutil.which(args[0]) is None:
        return None
    try:
        return subprocess.run(
            _host_argv(args),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise
    except OSError:
        return None


def _command_exists(name):
    if shutil.which(name):
        return True
    if not _in_flatpak():
        return False
    try:
        result = _run(["sh", "-c", "command -v " + shlex.quote(name)], timeout=5)
    except subprocess.TimeoutExpired:
        return False
    return result is not None and result.returncode == 0


def _busctl(args, timeout=3):
    """Call busctl --user. Returns CompletedProcess, or None if it cannot run."""
    try:
        return _run(["busctl", "--user", *args], timeout=timeout)
    except subprocess.TimeoutExpired:
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


def _read_kwinrc_text():
    """Host kwinrc. The sandbox does not see ~/.config."""
    path = _kwinrc()
    if _in_flatpak():
        try:
            result = _run(["cat", str(path)], timeout=5)
        except subprocess.TimeoutExpired:
            return ""
        if result is None or result.returncode != 0:
            return ""
        return result.stdout
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _read_nightcolor():
    text = _read_kwinrc_text()
    if not text:
        return {}
    section = {}
    in_group = False
    for line in text.splitlines():
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
        result = _run(cmd, timeout=8)
    except subprocess.TimeoutExpired as exc:
        return False, str(exc)
    if result is None:
        return False, "kwriteconfig6 is not available"
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
    if os.environ.get("FLUXV2_KEEP_NIGHTLIGHT") == "1":
        return True, ""
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
    if _command_exists("redshift"):
        return "redshift"
    if _command_exists("gammastep"):
        return "gammastep"
    return None


def run_cli(binary, temp=None, reset=False, disable=False):
    """One-shot redshift/gammastep call. reset and disable both use -x."""
    try:
        if reset or disable:
            cmd = [binary, "-x"]
        else:
            cmd = [binary, "-P", "-O", str(temp)]
        result = _run(cmd, timeout=10)
        if result is None:
            return False, f"{binary} is not installed"
        if result.returncode == 0:
            return True, ""
        return False, result.stderr.strip() or "Unknown error"
    except subprocess.TimeoutExpired:
        return False, f"{binary} timed out"
    except FileNotFoundError:
        return False, f"{binary} is not installed"
    except Exception as exc:
        return False, str(exc)
