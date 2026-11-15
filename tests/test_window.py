"""Window exits that do not depend on a working tray."""

from contextlib import nullcontext
from unittest import mock

import pytest

from fluxv2 import theme


def test_paper_preset_is_not_called_neutral():
    labels = {name for _temp, name in theme.PRESETS}
    assert (5500, "Paper") in theme.PRESETS
    assert "Neutral" not in labels


@pytest.fixture
def app(monkeypatch):
    pytest.importorskip("tkinter")
    import fluxv2.app as appmod

    for name, value in (
        ("set_temperature", lambda t: (True, None)),
        ("restore_display", lambda: (True, None)),
        ("describe_control", lambda: (True, "")),
        ("consume_note", lambda: ""),
        ("display_lock", nullcontext),
        ("exit_requested", lambda: False),
        ("updates_enabled", lambda: False),
        ("installable", lambda release: False),
        ("init_x11_threads", lambda: None),
    ):
        monkeypatch.setattr(appmod, name, value)
    monkeypatch.setattr("fluxv2.ui.tray_usable", lambda: True)
    monkeypatch.setattr("fluxv2.ui.menu_is_rendered", lambda: True)
    try:
        instance = appmod.FluxApp(lock=None)
    except Exception as exc:
        pytest.skip(f"cannot open a window: {exc}")
    yield instance
    try:
        instance.destroy()
    except Exception:
        pass


def test_failed_tray_reveals_quit(app):
    assert getattr(app, "_quit_button", None) is None

    class _Dead:
        error = RuntimeError("dock failed")

        def stop(self):
            return None

    app._tray_icon = _Dead()
    app._check_tray()
    assert app._quit_button is not None
    assert app._quit_button.cget("command") == app.quit_from_button
    assert "Quit exits" in app.hint_label.cget("text")


def test_exit_request_quits_once(app, monkeypatch):
    import fluxv2.app as appmod

    calls = []
    monkeypatch.setattr(appmod, "restore_display", lambda: calls.append("restore") or (True, None))
    monkeypatch.setattr(appmod, "exit_requested", lambda: True)
    app._poll_ui_queue()
    app._poll_ui_queue()
    assert calls == ["restore"]


def test_update_check_does_not_start_when_disabled(app, monkeypatch):
    import fluxv2.app as appmod

    started = []
    monkeypatch.setattr(appmod.threading, "Thread", lambda *a, **k: started.append(1))
    monkeypatch.setattr(appmod, "updates_enabled", lambda: False)
    app._start_update_check()
    assert started == []
