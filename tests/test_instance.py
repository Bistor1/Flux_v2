"""The second launch must not restore the screen, and signals only set a flag."""

import signal
from unittest import mock

import pytest

import fluxv2.backend as backend
from main import run


def test_non_owner_does_not_restore_or_open_a_window():
    with mock.patch("fluxv2.display.init_x11_threads") as init_threads, \
            mock.patch("fluxv2.instance.single_instance_or_signal", return_value=None), \
            mock.patch("fluxv2.backend.install_restore_hook") as restore, \
            mock.patch("fluxv2.backend.install_exit_signals") as signals, \
            mock.patch("fluxv2.app.FluxApp") as app:
        with pytest.raises(SystemExit) as caught:
            run()
    assert caught.value.code == 0
    init_threads.assert_called_once()
    restore.assert_not_called()
    signals.assert_not_called()
    app.assert_not_called()


def test_owner_restores_only_after_the_lock():
    order = []
    lock = mock.Mock()

    def mark(name):
        def _call(*_args, **_kwargs):
            order.append(name)
            return lock if name == "lock" else None
        return _call

    window = mock.Mock()

    with mock.patch("fluxv2.display.init_x11_threads", side_effect=mark("threads")), \
            mock.patch("fluxv2.instance.single_instance_or_signal", side_effect=mark("lock")), \
            mock.patch("fluxv2.backend.install_restore_hook", side_effect=mark("restore")), \
            mock.patch("fluxv2.backend.install_exit_signals", side_effect=mark("signals")), \
            mock.patch("fluxv2.app.FluxApp", return_value=window) as app:
        run()

    assert order == ["threads", "lock", "signals", "restore"]
    app.assert_called_once_with(lock=lock)
    window.mainloop.assert_called_once()
    lock.close.assert_called_once()


def test_signal_sets_a_flag_and_a_second_install_keeps_it():
    backend._exit_requested = False
    backend._signals_installed = False
    previous_term = signal.getsignal(signal.SIGTERM)
    previous_hup = signal.getsignal(signal.SIGHUP)
    try:
        assert backend.exit_requested() is False
        backend.install_exit_signals()
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)
        assert backend.exit_requested() is True
        backend.install_exit_signals()
        assert signal.getsignal(signal.SIGTERM) is handler
        assert backend.exit_requested() is True
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGHUP, previous_hup)
        backend._exit_requested = False
        backend._signals_installed = False
