# Improvements

Implemented in the working tree. The notes below are the review that drove the changes; the code and tests are the fix.

Reviewed the tree as of 2.0.18 (`main.py`, `fluxv2/`, the deb launcher, the Flatpak manifest, and `tests/`). Ranked by what actually fails for a user, not by style. Line numbers are the current sources.

## Fix these

### 1. A second launch undoes the tint — `main.py` 25–29, `fluxv2/backend.py` 26–39, `fluxv2/display.py` 864–891

`install_restore_hook()` runs **before** `single_instance_or_signal()`. It always calls `restore_display()`, and it registers an `atexit` hook that calls it again. A second `fluxv2` (double-click, menu click while it is already running) therefore:

1. Connects and restores, as if it owned the screen.
2. Loses the flock, sends `SHOW`, and exits 0.
3. Runs the atexit restore on the way out.

On KDE that is `_restore_icc()`: the second process puts the previous ICC profile back while the first process is still running and still says `On`. On X11 it writes the saved ramps back the same way. On wlroots it does not touch the compositor (the first process still holds `zwlr_gamma_control_v1`), but it does `_clear_state()` because it sees a `wlr` state file and no gamma objects of its own (`display.py` 877–881). The comment there assumes the previous process already disconnected. A live second launch is not that case.

**Change:** take the lock first. Only the owner may call `install_restore_hook()` or register the atexit restore. The loser sends `SHOW` and exits without opening Wayland or X11. Add a test that a non-owner never calls `restore_display`.

### 2. Logout leaves a KDE profile in place — no `SIGTERM` handler

`atexit` does not run on `SIGTERM`. A desktop logout, `kill`, or session teardown therefore skips both `_really_quit()` and `_restore_on_exit()`.

That is harmless for wlroots: the compositor restores gamma when the socket closes. It is not harmless for KDE. The ICC path is a KWin output setting, not a resource held by this process. After logout or reboot the `temperature-*.icc` profile is still selected until the next Flux start happens to restore it. X11 gamma dies with the X server, but a `SIGTERM` while the session stays up leaves Flux's ramp on until something else writes gamma.

**Change:** handle `SIGTERM` and `SIGHUP` on the main thread (set a flag, `after()` into `_really_quit()`, or call `restore_display()` under `display_lock` and then exit). Do not restore when `FLUXV2_KEEP_TINT=1`. wlroots can keep relying on disconnect; KDE and X11 cannot.

### 3. A dead Wayland connection is reused forever — `fluxv2/display.py` 773–776, 608–648, 530–548

`_ensure_wayland()` treats any non-NULL `display` plus a remembered `kind` as healthy. `wl_display_roundtrip` / `wl_display_flush` returning -1 means that connection is dead (compositor restart, protocol error, crashed KWin). Nothing clears `self.wayland`, so every later slider tick fails the same way until the process is restarted.

**Change:** on flush or roundtrip failure, `close()` the client, set `self.wayland = None`, and let the next `ensure()` connect again. Do this before returning the error, so one failed apply does not poison the session. Same for a `failed` gamma event on every output: `release_gamma()` and retry `ensure_gamma()` once, instead of reporting the error until restart.

### 4. X11 gamma is not safe next to Tk — `fluxv2/display.py` 839–849, `fluxv2/app.py` 419–431

Two separate problems:

- Gamma is applied on a worker thread. Tk is already using Xlib on the main thread. Xlib is not thread-safe unless `XInitThreads()` ran before any other X call, including Tk's. That call is missing, so an apply can crash or corrupt the display on X11.
- `_install_x_handler()` replaces the process-wide `XSetErrorHandler` and drops the previous handler. Any X error, including Tk's, sets `_x_error` and is swallowed. A later `XRRSetCrtcGamma` can then be reported as rejected because Tk tripped the flag, or a real Tk error can be ignored.

**Change:** call `XInitThreads()` once, before `CTk()` and before `XOpenDisplay`. Save the previous error handler and chain to it. Set `_x_error` only around `XRRSetCrtcGamma` + `XSync`, and clear it at the start of that section so a stale flag cannot fail the next write. If one CRTC in `_apply_x11` fails after earlier CRTCs were already written (`display.py` 1048–1054), roll those back from the snapshot before returning the error. Today the UI says the apply failed while some outputs are already tinted.

### 5. A failed tray dock can remove the only Quit — `fluxv2/ui.py` 162–174, `fluxv2/app.py` 303–323 and 367–374

The Quit button is decided once, at layout time. It is omitted when `tray_usable()` and `menu_is_rendered()` are both true. Docking is checked later, in `_check_tray`. If that check finds the loop dead, the icon is dropped and `_tray_live` stays false, but the Quit button is not added.

`_on_close` then only iconifies, on purpose, so a kill is not the way out. With no tray menu and no Quit button, the taskbar close is also `WM_DELETE_WINDOW`, which iconifies again. The only remaining exit is killing the process, which skips `restore_display()` (see item 2). Launching Flux again only sends `SHOW`; it does not quit the stuck process.

This does not bite a Wayland session today, because `tray_usable()` is already false there and the button is packed. It does bite an X11 session whose backend claims `HAS_MENU` and then fails to dock.

**Change:** on the tray-failed path, pack the same Quit button `_quit()` would have packed, and rewrite the hint. Or always show Quit. Add a regression test: menu-capable tray, `handle.error` set, close does not leave the window without a quit callback.

## Worth doing

### 6. KDE Wayland never gets a tray — `fluxv2/tray.py` 43–66, `requirements.txt`, `flatpak/io.github.Bistor1.FluxV2.yml`

`tray_usable()` rejects every Wayland session, not just the XEmbed backend. That guard is right for `_xorg`: `DISPLAY` is set under XWayland and still cannot dock into a StatusNotifier host. It is wrong for `_appindicator` / `_gtk`, which register a StatusNotifierItem on the session bus. KDE already shows those on Wayland. The Flatpak manifest already has `--talk-name=org.kde.StatusNotifierWatcher`, and nothing in the app uses it.

On a typical install the resolved backend is still `_xorg`, because `gi` is not installed. So deleting the Wayland check alone would not show an icon; it would only let XEmbed try and fail.

**Change:** keep rejecting `_xorg` (and a missing `DISPLAY`) on Wayland. If the backend is AppIndicator or GTK, allow it. Ship that backend: `python3-gi` plus `gir1.2-ayatanaappindicator3-0.1` on the deb, and the matching runtime modules in the Flatpak. Do not pretend `import pystray` is enough.

### 7. A monitor plugged in later is never tinted — `fluxv2/display.py` 416–430, 560–566, 909–914

Outputs are bound once, at connect. `ensure_gamma()` returns immediately when any control is still live, so a docked monitor never gets a `zwlr_gamma_control_v1`. The KDE path is the same shape: a new `output` event sits in the Wayland queue, and `_apply_icc` only roundtrips first when **no** device is enabled. With one monitor already on, the new device is dispatched during `apply_marked()`'s roundtrip, which is after the device list for that apply was built.

**Change:** before each apply, roundtrip, and if the output list changed, create gamma controls or ICC updates for the new devices only. On `global_remove` / `removed`, drop that output instead of writing to a dead proxy.

### 8. HDR is detected and then skipped — `fluxv2/display.py` 925–927, opcodes at 740–741

`_apply_icc` sets the SDR ICC path and source only. If any enabled output has HDR on, the status line says the profile may not change the picture, and that is the end of it. The configuration object already has `set_hdr_icc_profile_path` and `set_hdr_color_profile_source`, and they are not sent.

**Change:** when `hdr` is set, mark the HDR path and source as well, with the same file, and say so in the note. Do not clear an HDR profile Flux did not set; snapshot `hdr_icc_profile_path` the same way `_snapshot_icc` snapshots the SDR path.

### 9. Every slider stop writes another ICC file — `fluxv2/display.py` 162–166, `fluxv2/gamma.py` `write_temperature_profile`

Each distinct temperature becomes `~/.local/share/fluxv2/temperature-<N>.icc`. Nothing deletes the previous file. A drag across the slider is one file per 350 ms stop, and they survive quit. The distinct path is deliberate: KWin reloads a profile when the path changes, so overwriting one filename is not enough.

**Change:** after a successful apply, unlink Flux profiles other than the one just set. Keep the path-per-temperature trick.

### 10. The updater installs whatever GitHub redirected to — `fluxv2/update.py` 155–184 and 186–228, `fluxv2/app.py` 89–107

The host allowlist is the right idea, and the Flatpak path checks `%{url_effective}`. Neither path checks the asset digest. The GitHub release API puts `sha256:…` on each asset. A `.deb` is then installed with `pkexec`. Verify the digest before `dpkg` / `flatpak install`, and refuse a release that has no digest.

Related, smaller:

- `current_is_outdated()` prompts even when the matching asset URL is missing. Require `deb_url` or `flatpak_url` for the way the process is running before showing the dialog.
- A source checkout (`python main.py`) is offered a system `.deb` install. Skip the check unless `FLATPAK_ID` is set or the process was started from the installed launcher. `FLUXV2_NO_UPDATE` already exists; the launcher should not be the only place that knows to set it.
- After a failed install, `_update_prompted` stays true, so the user is not asked again until restart. Clear it in `_update_failed`.

### 11. "Neutral" is two temperatures — `fluxv2/theme.py` 8–16, `fluxv2/ui.py` 145–150

The preset labeled Neutral is 5500K. The button labeled `Neutral (6500K)` is the identity ramp. 5500K is not neutral in this app; 6500K is. Rename the preset (for example `5500K / Paper`) so the button and the grid do not contradict each other.

### 12. The window cannot shrink, and Quit sits at the bottom — `fluxv2/app.py` 55–56

`geometry("560x760")` and `resizable(False, False)`. On Wayland the Quit button is the only exit, and it is the last widget. A short laptop panel, or CustomTkinter's DPI scaling, puts that button below the work area with no way to resize. Allow vertical resize with a minimum size, or fit to the work area on map and keep Quit in view.

### 13. The risky code has no tests — `tests/`

`tests/test_gamma.py` and `tests/test_tray.py` cover the ramp table and the tray fallbacks. They do not cover:

- non-owner must not restore (item 1)
- `restore()` given a `wlr` / `icc` / `x11` state file when this process holds no connection
- reconnect after a failed roundtrip (item 3)
- Quit appearing after a failed dock (item 5)
- `parse_version` / `is_newer` / URL allowlist / digest refusal in `update.py`
- socket `SHOW` round-trip in `instance.py`

`display.py` is about 1200 lines of protocol code and the only proof it works is a live session. The state-file and restore decisions can be tested by faking `Session` without a compositor.

## Leave alone

These look like gaps and are not:

- **GNOME Wayland.** No gamma protocol, and the app correctly refuses to drive Night Light or to tint via XWayland. Do not add that fallback.
- **A normal launch stays off.** `FLUXV2_RESUME` is only for the update restart. Do not reapply the last temperature on every start unless that becomes an explicit setting.
- **6500K is an identity ramp, not "off".** Turn Off is the restore path. Do not merge those buttons.
- **The X11 tray has no menu.** `HAS_MENU` is false, and the window Quit button is the replacement. Do not expect a right-click Quit there.
- **A slow tray dock is not a failed dock.** The watchdog waits until `run()` returns. Do not put the timeout back on `docked`.
- **One ramp for every output.** Per-monitor temperature is a different product. The partial-apply note (`N of M outputs`) is the right behaviour for a shared slider.
