"""Display helpers that do not need a compositor."""

import os

from fluxv2 import display


def test_pending_output_names_skips_bound_ids():
    known = [(1, 4), (2, 4), (3, 3)]
    assert display.pending_output_names(known, {1, 3}) == [2]
    assert display.pending_output_names(known, set()) == [1, 2, 3]


def test_prune_deletes_siblings_and_keeps_the_applied_file(tmp_path, monkeypatch):
    monkeypatch.setattr(display, "profile_dir", lambda: str(tmp_path))
    keep = tmp_path / "temperature-3000.icc"
    other = tmp_path / "temperature-2700.icc"
    stranger = tmp_path / "notes.txt"
    keep.write_bytes(b"keep")
    other.write_bytes(b"old")
    stranger.write_bytes(b"leave")
    outside = tmp_path / "outside"
    outside.mkdir()
    leaked = outside / "temperature-1.icc"
    leaked.write_bytes(b"no")

    display.prune_temperature_profiles(str(keep))

    assert keep.is_file()
    assert not other.exists()
    assert stranger.is_file()
    assert leaked.is_file()


def test_prune_does_not_follow_a_keep_path_outside_the_profile_dir(tmp_path, monkeypatch):
    profile = tmp_path / "profiles"
    profile.mkdir()
    monkeypatch.setattr(display, "profile_dir", lambda: str(profile))
    inside = profile / "temperature-3000.icc"
    inside.write_bytes(b"ours")
    outside = tmp_path / "temperature-3000.icc"
    outside.write_bytes(b"not ours")

    display.prune_temperature_profiles(str(outside))

    # keep is not in the profile dir, so it is not a reason to walk outside,
    # and the copy inside the dir is not the file we were told to keep.
    assert not inside.exists()
    assert outside.is_file()


def test_drop_wayland_closes_and_clears_the_client():
    closed = []

    class _Client:
        def close(self):
            closed.append(1)

    session = display.Session()
    session.wayland = _Client()
    session.drop_wayland()
    assert closed == [1]
    assert session.wayland is None
    session.drop_wayland()


def test_init_x11_threads_can_be_called_twice():
    display.init_x11_threads()
    display.init_x11_threads()
