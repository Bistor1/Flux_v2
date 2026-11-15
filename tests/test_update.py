"""Updater decisions. No network, no pkexec."""

import hashlib
import sys
from unittest import mock

import pytest

from fluxv2 import update
from fluxv2.update import Release


def test_parse_version_ignores_a_non_numeric_tail():
    assert update.parse_version("v2.0.9") < update.parse_version("v2.0.18")
    assert not update.is_newer("2.0.18", "2.0.18")
    assert update.parse_version("2.0.18-deb1") == (2, 0, 18)


def test_safe_url_stays_on_the_allowlist():
    assert update._safe_url("https://github.com/Bistor1/Flux_v2/releases/download/x")
    assert update._safe_url("https://release-assets.githubusercontent.com/file")
    assert not update._safe_url("http://github.com/Bistor1/Flux_v2/file")
    assert not update._safe_url("https://example.com/fluxv2.deb")


def test_updates_enabled_skips_source_and_the_env_flag(monkeypatch):
    monkeypatch.delenv("FLUXV2_NO_UPDATE", raising=False)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(sys, "argv", ["/home/user/Flux-v2/main.py"])
    assert update.updates_enabled() is False

    monkeypatch.setenv("FLATPAK_ID", "io.github.Bistor1.FluxV2")
    assert update.updates_enabled() is True
    monkeypatch.setenv("FLUXV2_NO_UPDATE", "1")
    assert update.updates_enabled() is False

    monkeypatch.delenv("FLUXV2_NO_UPDATE", raising=False)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(sys, "argv", ["/usr/share/fluxv2/main.py"])
    assert update.updates_enabled() is True


def test_installable_requires_digest_and_the_mode_url(monkeypatch):
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    digest = "ab" * 32
    deb = Release("9.0.0", "https://github.com/Bistor1/file.deb", None, digest)
    assert update.installable(deb) is True
    assert update.installable(Release("9.0.0", "https://github.com/x.deb", None, None)) is False
    assert update.installable(Release("9.0.0", "http://github.com/x.deb", None, digest)) is False
    assert update.installable(None) is False

    monkeypatch.setenv("FLATPAK_ID", "io.github.Bistor1.FluxV2")
    assert update.installable(deb) is False
    flatpak = Release("9.0.0", None, "https://github.com/Bistor1/file.flatpak", digest)
    assert update.installable(flatpak) is True


def test_wrong_digest_never_runs_dpkg(monkeypatch, tmp_path):
    monkeypatch.setattr(update, "_private_dir", lambda: str(tmp_path))

    def fake_download(_url, dest):
        with open(dest, "wb") as handle:
            handle.write(b"not-the-package")

    monkeypatch.setattr(update, "_download", fake_download)
    called = []
    monkeypatch.setattr(update.subprocess, "run", lambda *args, **kwargs: called.append(args))
    release = Release(
        "9.9.9",
        "https://github.com/Bistor1/Flux_v2/releases/download/v9.9.9/flux.deb",
        None,
        "cd" * 32,
    )
    ok, err = update._install_deb(release)
    assert ok is False
    assert "digest" in err
    assert called == []


def test_matching_digest_may_call_the_installer(monkeypatch, tmp_path):
    monkeypatch.setattr(update, "_private_dir", lambda: str(tmp_path))
    payload = b"package-bytes"

    def fake_download(_url, dest):
        with open(dest, "wb") as handle:
            handle.write(payload)

    monkeypatch.setattr(update, "_download", fake_download)
    monkeypatch.setattr(update.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        update.subprocess,
        "run",
        lambda *args, **kwargs: mock.Mock(returncode=0, stderr="", stdout=""),
    )
    release = Release(
        "9.9.9",
        "https://github.com/Bistor1/Flux_v2/releases/download/v9.9.9/flux.deb",
        None,
        hashlib.sha256(payload).hexdigest(),
    )
    ok, err = update._install_deb(release)
    assert (ok, err) == (True, "")


def test_current_is_outdated_rejects_a_release_without_a_digest(monkeypatch):
    monkeypatch.setattr(
        update,
        "fetch_latest",
        lambda: Release("99.0.0", "https://github.com/Bistor1/file.deb", None, None),
    )
    assert update.current_is_outdated() is None
