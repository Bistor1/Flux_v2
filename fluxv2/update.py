"""Check GitHub releases and install a newer package if the user agrees."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass

# Running this file directly puts fluxv2/ on sys.path, not the project root.
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fluxv2 import __version__

REPO = "Bistor1/Flux_v2"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
USER_AGENT = "fluxv2-updater"
_ALLOWED_HOSTS = (
    "https://github.com/",
    "https://release-assets.githubusercontent.com/",
    "https://objects.githubusercontent.com/",
    "https://github-releases.githubusercontent.com/",
)


@dataclass(frozen=True)
class Release:
    version: str
    deb_url: str | None
    flatpak_url: str | None


def parse_version(text):
    """Turn 'v2.0.14' into (2, 0, 14). Non-numeric tails are ignored."""
    parts = []
    for piece in str(text).lstrip("vV").split("."):
        digits = ""
        for char in piece:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_newer(remote, local):
    return parse_version(remote) > parse_version(local)


def fetch_latest(timeout=15):
    """Latest GitHub release, or None when the request fails."""
    request = urllib.request.Request(
        API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    version = str(payload.get("tag_name") or "").lstrip("vV")
    if not version:
        return None
    deb_url = None
    flatpak_url = None
    for asset in payload.get("assets") or []:
        name = asset.get("name") or ""
        url = asset.get("browser_download_url") or ""
        if not _safe_url(url):
            continue
        if name == f"fluxv2_{version}_amd64.deb":
            deb_url = url
        elif name == f"fluxv2_{version}.flatpak":
            flatpak_url = url
    return Release(version=version, deb_url=deb_url, flatpak_url=flatpak_url)


def current_is_outdated():
    release = fetch_latest()
    if release is None or not is_newer(release.version, __version__):
        return None
    return release


def _safe_url(url):
    return any(url.startswith(prefix) for prefix in _ALLOWED_HOSTS)


def _in_flatpak():
    return bool(os.environ.get("FLATPAK_ID"))


def _host(args, timeout=180):
    """Run a command on the host, even from inside the Flatpak sandbox."""
    if _in_flatpak() and shutil.which("flatpak-spawn"):
        args = ["flatpak-spawn", "--host", *args]
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _download(url, dest):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response, open(dest, "wb") as handle:
        shutil.copyfileobj(response, handle)
    os.chmod(dest, 0o644)


def install_release(release):
    """Download and install the package that matches how Flux is running.

    Returns (ok, error). A .deb install asks for a password through pkexec.
    """
    if _in_flatpak():
        return _install_flatpak(release)
    return _install_deb(release)


def _install_deb(release):
    if not release.deb_url:
        return False, "The release has no .deb package."
    path = os.path.join(tempfile.gettempdir(), f"fluxv2_{release.version}_amd64.deb")
    try:
        _download(release.deb_url, path)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"Download failed: {exc}"
    installer = "pkexec" if shutil.which("pkexec") else "sudo"
    if not shutil.which(installer) or not shutil.which("dpkg"):
        return False, f"Downloaded to {path}. Install it with: sudo dpkg -i {path}"
    try:
        result = subprocess.run(
            [installer, "dpkg", "-i", path],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, str(exc)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "dpkg failed").strip()
        return False, err
    return True, ""


def _install_flatpak(release):
    if not release.flatpak_url:
        return False, "The release has no Flatpak bundle."
    dest = f"/tmp/fluxv2_{release.version}.flatpak"
    try:
        result = _host(["curl", "-fsSL", "-o", dest, release.flatpak_url], timeout=180)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, str(exc)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "Download failed").strip()
        return False, err
    try:
        result = _host(
            ["flatpak", "install", "--user", "--noninteractive", "--or-update", dest],
            timeout=180,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, str(exc)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "flatpak install failed").strip()
        return False, err
    return True, ""


def relaunch_command():
    """Command that starts the newly installed copy."""
    if _in_flatpak():
        return ["flatpak", "run", "io.github.Bistor1.FluxV2"]
    launcher = "/usr/bin/fluxv2"
    if os.path.isfile(launcher) and os.access(launcher, os.X_OK):
        return [launcher]
    return None


def main():
    release = fetch_latest()
    if release is None:
        print("Could not check for updates.", file=sys.stderr)
        return 1
    if is_newer(release.version, __version__):
        print(f"Update available: {release.version} (installed: {__version__})")
        return 0
    print(f"Flux v2 {__version__} is up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
