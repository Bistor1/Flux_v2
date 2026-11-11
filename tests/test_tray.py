"""Regression tests for the system tray fallbacks.

The tray is unavailable on a Wayland session, so every path around it has to
degrade without stranding the tinted screen. These cover the decisions in
fluxv2.tray and the two window behaviours that depend on them.
"""

import os
import threading
import time

import pytest

from fluxv2 import tray
from fluxv2.app import TRAY_THREAD_NAME, FluxApp


@pytest.fixture
def clean_env(monkeypatch):
    for var in ("XDG_SESSION_TYPE", "DISPLAY", "PYSTRAY_BACKEND", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


class TestBackendProbe:
    """`import pystray` succeeding says nothing about a usable tray."""

    def test_backend_name_is_read_from_the_icon_class(self):
        # __init__ deletes the backend() helper, so this is the only handle.
        assert tray.TRAY_BACKEND_NAME.startswith("pystray.")

    def test_wayland_is_rejected_even_with_a_display(self, clean_env):
        # XWayland always sets DISPLAY, so the socket proves nothing.
        clean_env.setenv("XDG_SESSION_TYPE", "wayland")
        clean_env.setenv("DISPLAY", ":0")
        assert tray.tray_usable() is False
        assert "Wayland" in tray.tray_reason()

    def test_x11_with_display_is_accepted(self, clean_env):
        clean_env.setenv("XDG_SESSION_TYPE", "x11")
        clean_env.setenv("DISPLAY", ":0")
        assert tray.tray_usable() is True
        assert tray.tray_reason() == ""

    def test_missing_display_is_rejected(self, clean_env):
        clean_env.setenv("XDG_SESSION_TYPE", "x11")
        assert tray.tray_usable() is False
        assert "DISPLAY" in tray.tray_reason()

    def test_dummy_backend_is_not_a_tray(self, clean_env):
        clean_env.setenv("PYSTRAY_BACKEND", "dummy")
        clean_env.setenv("XDG_SESSION_TYPE", "x11")
        clean_env.setenv("DISPLAY", ":0")
        assert tray.tray_usable() is False

    def test_unavailable_when_import_failed(self, monkeypatch):
        monkeypatch.setattr(tray, "PYSTRAY_AVAILABLE", False)
        monkeypatch.setattr(tray, "_IMPORT_ERROR", "boom")
        assert tray.tray_usable() is False
        assert tray.tray_reason() == "boom"


class TestMenuIsRendered:
    def test_xorg_renders_no_menu(self):
        # _xorg.Icon.HAS_MENU is False, so a Quit menu item is unreachable.
        if not tray.PYSTRAY_AVAILABLE or not tray.TRAY_BACKEND_NAME.endswith("_xorg"):
            pytest.skip("X11 backend not selected on this host")
        assert tray.menu_is_rendered() is False

    def test_unknown_backend_has_no_menu(self, monkeypatch):
        monkeypatch.setattr(tray, "_tray_class", lambda: None)
        assert tray.menu_is_rendered() is False


class _DeadIcon:
    """Stands in for an icon that cannot dock."""

    HAS_MENU = False

    def __init__(self):
        self.stopped = False

    def run(self, setup=None):
        raise AssertionError("Failed to dock icon")

    def stop(self):
        self.stopped = True


@pytest.fixture
def failing_pystray(monkeypatch):
    icons = []

    class FakeMenu:
        SEPARATOR = None

        def __init__(self, *a, **k):
            pass

    fake = type("fake_pystray", (), {})
    fake.Menu = FakeMenu
    fake.MenuItem = lambda *a, **k: None

    def make_icon(*a, **k):
        icon = _DeadIcon()
        icons.append(icon)
        return icon

    fake.Icon = make_icon
    monkeypatch.setattr(tray, "pystray", fake)
    monkeypatch.setattr(tray, "PYSTRAY_AVAILABLE", True)
    return icons


class TestStartTray:
    def test_no_image_means_no_tray(self):
        assert tray.start_tray(None, lambda: None, lambda: None) is None

    def test_unavailable_backend_means_no_tray(self, monkeypatch):
        monkeypatch.setattr(tray, "PYSTRAY_AVAILABLE", False)
        assert tray.start_tray(object(), lambda: None, lambda: None) is None

    def test_failed_dock_is_reported_not_swallowed(self, failing_pystray):
        # pystray's own except in _run() eats this error and start_tray would
        # otherwise hand back an icon that never appeared.
        handle = tray.start_tray(object(), lambda: None, lambda: None)
        assert handle is not None
        assert "dock" in str(handle.error)

    def test_failed_dock_stops_the_icon(self, failing_pystray):
        handle = tray.start_tray(object(), lambda: None, lambda: None)
        assert handle.error is not None
        # _watch stops the icon on a separate thread.
        for _ in range(100):
            if failing_pystray and failing_pystray[0].stopped:
                return
            time.sleep(0.05)
        pytest.fail("the undockable icon was never stopped")

    def test_tray_thread_is_named_for_the_crash_guard(self, monkeypatch):
        started = {}

        class IdleIcon:
            HAS_MENU = True

            def run(self, setup=None):
                started["thread"] = threading.current_thread().name
                time.sleep(5)

            def stop(self):
                pass

        class FakeMenu:
            SEPARATOR = None

            def __init__(self, *a, **k):
                pass

        fake = type("fake_pystray", (), {})
        fake.Menu = FakeMenu
        fake.MenuItem = lambda *a, **k: None
        fake.Icon = lambda *a, **k: IdleIcon()
        monkeypatch.setattr(tray, "pystray", fake)
        monkeypatch.setattr(tray, "PYSTRAY_AVAILABLE", True)

        handle = tray.start_tray(object(), lambda: None, lambda: None)
        for _ in range(100):
            if "thread" in started:
                break
            time.sleep(0.05)
        assert started["thread"] == TRAY_THREAD_NAME
        handle.stop()

    def test_slow_dock_is_not_treated_as_a_failure(self, monkeypatch):
        # A tray manager can take longer than the watchdog window to hand back
        # an XEmbed socket. Killing it there turns a working tray into a dead
        # one, so the watchdog may only stop an icon whose run() has returned.
        icons = []

        class SlowIcon:
            HAS_MENU = True

            def __init__(self):
                self.stopped = False
                self.entered = threading.Event()

            def run(self, setup=None):
                self.entered.set()
                time.sleep(20)

            def stop(self):
                self.stopped = True

        class FakeMenu:
            SEPARATOR = None

            def __init__(self, *a, **k):
                pass

        def make_icon(*a, **k):
            icon = SlowIcon()
            icons.append(icon)
            return icon

        fake = type("fake_pystray", (), {})
        fake.Menu = FakeMenu
        fake.MenuItem = lambda *a, **k: None
        fake.Icon = make_icon
        monkeypatch.setattr(tray, "pystray", fake)
        monkeypatch.setattr(tray, "PYSTRAY_AVAILABLE", True)

        handle = tray.start_tray(object(), lambda: None, lambda: None)
        assert icons[0].entered.wait(5)
        # Past the old 5s timeout, with the loop still healthy and undocked.
        time.sleep(6.0)
        assert icons[0].stopped is False
        assert handle.error is None
        handle.stop()


class TestThreadGuard:
    def test_tray_thread_crash_keeps_the_process_alive(self, capsys):
        FluxApp._install_thread_guard()

        def boom():
            raise RuntimeError("tray exploded")

        thread = threading.Thread(
            target=boom, daemon=True, name=TRAY_THREAD_NAME
        )
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert "tray exploded" in capsys.readouterr().err


class TestWindowFallbacks:
    """No tray means the window is the only way out, so it must stay usable."""

    @pytest.fixture
    def app(self):
        pytest.importorskip("tkinter")
        from contextlib import nullcontext
        from unittest import mock

        import fluxv2.app as appmod

        for name, value in (
            ("set_temperature", lambda t: (True, None)),
            ("restore_display", lambda: (True, None)),
            ("describe_control", lambda: (True, "")),
            ("consume_note", lambda: ""),
            ("display_lock", nullcontext),
        ):
            mock.patch.object(appmod, name, value).start()
        try:
            instance = appmod.FluxApp(lock=None)
        except Exception as exc:  # no display available
            pytest.skip(f"cannot open a window: {exc}")
        yield instance
        mock.patch.stopall()

    def test_close_minimises_without_a_tray(self, app):
        app._tray_live = False
        app._on_close()
        app.update_idletasks()
        assert app.state() == "iconic"

    def test_quit_restores_the_display(self, app):
        import fluxv2.app as appmod

        restored = []
        appmod.restore_display = lambda: (restored.append(1), (True, None))[1]
        app.quit_from_button()
        assert restored
