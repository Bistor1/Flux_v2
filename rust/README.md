# Flux v2, Rust

Same program as the Python app: a slider, the Redshift blackbody table, wlroots gamma-control, KDE ICC profiles, and X11 RandR. It lives in this folder so the installed Python package is left alone.

## Run

```bash
cargo run --release
```

A second launch only asks the running window to show itself. It does not touch the screen. State and profiles are under `fluxv2-rs`, not `fluxv2`, so this build does not restore or overwrite the pacman install.

`6500K` is the identity ramp. Turn Off puts the previous gamma or ICC profile back. `SIGTERM` does that too, unless `FLUXV2_KEEP_TINT=1`. `FLUXV2_RESUME=1` reapplies the saved temperature.

## Why this is the fast path

The slider does not start a thread per tick. One worker owns the display connection and, if several temperatures are queued, applies only the latest. Gamma bytes are written into a reused buffer. An ICC file is written once per temperature and then reused. The release profile is fat-LTO, one codegen unit, stripped.

GNOME Wayland is still unsupported. There is no protocol to drive, and this build does not fall back to XWayland gamma or Night Light.

The window is the control. A StatusNotifier icon is not wired up yet; close quits and restores.

On an RPM, deb, or Flatpak install, startup checks GitHub for a newer release. Install is offered only when the asset digest matches. A copy in `target/` does not check. Set `FLUXV2_NO_UPDATE=1` to skip it. The Fedora asset name is `fluxv2-<version>-1.fc43.x86_64.rpm`.
