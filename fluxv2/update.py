"""Check GitHub releases and install a newer package if the user agrees."""

import hashlib
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
    digest: str | None = None


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
    deb_digest = None
    flatpak_digest = None
    for asset in payload.get("assets") or []:
        name = asset.get("name") or ""
        url = asset.get("browser_download_url") or ""
        if not _safe_url(url):
            continue
        if name == f"fluxv2_{version}_amd64.deb":
            deb_url = url
            deb_digest = _digest_hex(asset.get("digest"))
        elif name == f"fluxv2_{version}.flatpak":
            flatpak_url = url
            flatpak_digest = _digest_hex(asset.get("digest"))
    digest = flatpak_digest if _in_flatpak() else deb_digest
    return Release(
        version=version,
        deb_url=deb_url,
        flatpak_url=flatpak_url,
        digest=digest,
    )


def _digest_hex(value):
    if not isinstance(value, str) or not value.lower().startswith("sha256:"):
        return None
    hexpart = value.split(":", 1)[1].strip().lower()
    if len(hexpart) != 64 or any(char not in "0123456789abcdef" for char in hexpart):
        return None
    return hexpart


def updates_enabled():
    """False for a source checkout or when FLUXV2_NO_UPDATE is set."""
    if os.environ.get("FLUXV2_NO_UPDATE"):
        return False
    if os.environ.get("FLATPAK_ID"):
        return True
    argv0 = os.path.realpath(sys.argv[0])
    return argv0 in ("/usr/share/fluxv2/main.py", "/usr/bin/fluxv2")


def installable(release):
    """True when this process can verify and install the release."""
    if release is None or not release.digest:
        return False
    url = release.flatpak_url if _in_flatpak() else release.deb_url
    return bool(url) and _safe_url(url)


def current_is_outdated():
    release = fetch_latest()
    if release is None or not is_newer(release.version, __version__):
        return None
    if not installable(release):
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


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _safe_url(newurl):
            raise urllib.error.URLError(f"refusing redirect to {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener():
    return urllib.request.build_opener(_SafeRedirect)


def _private_dir():
    parent = os.environ.get("XDG_RUNTIME_DIR") or None
    directory = tempfile.mkdtemp(prefix="fluxv2-", dir=parent)
    os.chmod(directory, 0o700)
    return directory


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest_matches(path, expected):
    if not expected:
        return False
    return _sha256_file(path).lower() == expected.lower()


def _remote_digest_matches(path, expected):
    """Hash a file that may live on the host outside the Flatpak sandbox."""
    if not expected:
        return False
    if not _in_flatpak():
        return _digest_matches(path, expected)
    try:
        result = _host(["sha256sum", path], timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        return False
    if result.returncode != 0:
        return False
    got = (result.stdout or "").split()
    return bool(got) and got[0].lower() == expected.lower()


def _download(url, dest):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with _opener().open(request, timeout=120) as response, open(dest, "wb") as handle:
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
    if not release.digest:
        return False, "The release has no sha256 digest."
    directory = _private_dir()
    path = os.path.join(directory, f"fluxv2_{release.version}_amd64.deb")
    try:
        _download(release.deb_url, path)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        shutil.rmtree(directory, ignore_errors=True)
        return False, f"Download failed: {exc}"
    if not _digest_matches(path, release.digest):
        shutil.rmtree(directory, ignore_errors=True)
        return False, "The download digest did not match the release."
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
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "dpkg failed").strip()
        return False, err
    return True, ""


def _install_flatpak(release):
    if not release.flatpak_url:
        return False, "The release has no Flatpak bundle."
    if not release.digest:
        return False, "The release has no sha256 digest."
    if not _safe_url(release.flatpak_url):
        return False, "The download URL is not on an allowed host."
    parent = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    try:
        made = _host(["mktemp", "-d", "-p", parent, "fluxv2.XXXXXXXX"], timeout=30)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, str(exc)
    if made.returncode != 0:
        return False, (made.stderr or made.stdout or "Could not create a download directory").strip()
    directory = made.stdout.strip()
    dest = os.path.join(directory, f"fluxv2_{release.version}.flatpak")
    try:
        result = _host(
            [
                "curl", "-fsSL", "--proto", "=https",
                "-o", dest,
                "-w", "%{url_effective}",
                release.flatpak_url,
            ],
            timeout=180,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "Download failed").strip()
            return False, err
        final_url = (result.stdout or "").strip()
        if not _safe_url(final_url):
            return False, "The download was redirected to a host Flux will not use."
        if not _remote_digest_matches(dest, release.digest):
            return False, "The download digest did not match the release."
        result = _host(
            ["flatpak", "install", "--user", "--noninteractive", "--or-update", dest],
            timeout=180,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, str(exc)
    finally:
        _host(["rm", "-rf", directory], timeout=30)
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
