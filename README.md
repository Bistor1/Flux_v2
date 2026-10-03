# Flux v2

Modern screen color temperature control for Linux.

A clean, modern GUI built with CustomTkinter — dark theme, live slider, and a temperature-gradient logo.

## Original author
The original author of the unpublished v1 of Flux is: "CartoonRacoon"

## Features

- **Modern UI**: Dark theme with warm-to-cool gradient accents (orange `#f59e0b` ↔ blue `#3b82f6`), large temperature display that recolors with the temperature
- **Live Slider**: Moving the slider applies the temperature automatically (~350 ms after you stop dragging) — no need to click Apply
- **Quick Presets**: From Daylight (6500K) to Deep Red (1800K) in a clean 2×4 grid
- **Color temperature**: Our own gamma ramps, the same factors on every output. 6500K is an identity ramp, not "off". No redshift package and no Night Light
- **Actions**: Reset to normal, turn the tint off
- **System Tray**: Minimizes to tray on window close; tray menu offers **Open** and **Quit** (requires `pystray`, auto-installed on first run)
- **Single Instance**: Launching a second time brings the running window to the front instead of creating a duplicate tray icon
- **Updates**: On startup, checks GitHub for a newer release and asks "Do you want to update?" before installing it
- **First-Run Setup Wizard**: On first launch, Flux v2 opens a pure-tkinter window with a live checklist that creates the venv and installs all required Python packages (customtkinter, Pillow, pystray) step-by-step
- **Logo**: Single circular temperature-gradient icon (warm red center → orange → cool blue rim)
- **Debian Package**: Complete `.deb` with launcher, automatic setup, and icon
- **Flatpak**: `io.github.Bistor1.FluxV2` manifest, with Tcl/Tk bundled so it does not need the host `python3-tk`

## Requirements

- Linux (Debian/Ubuntu recommended)
- One of: a wlroots compositor (Sway, Hyprland, niri, …), KDE Plasma with output-management v8 or newer, or an X11 session with XRandR
- GNOME Wayland is not supported. It does not expose a gamma protocol, and Flux does not drive Night Light
- Python 3.11+
- `python3-venv`, `python3-tk`, `libwayland-client0`, `liblcms2-2` (KDE profiles), `libxrandr2` (X11)

## Installation

### Via .deb package (recommended)

```bash
sudo dpkg -i fluxv2_2.0.17_amd64.deb
sudo apt install -f
```

Then launch `fluxv2` from the menu or terminal. On first launch the setup wizard downloads dependencies.

### From source (development)

```bash
cd Flux-v2
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

In VS Code, quit and reopen the editor once so the Python debugger is allowed to start, then press F5 (**Flux v2**). That uses `.venv` and the sources in the tree, so you do not need to rebuild the Flatpak. Until then, **Terminal → Run Task → Flux v2** (or Ctrl+Shift+B) starts the same command.

## Usage

1. Drag the **slider** → that temperature is applied about 350 ms after you stop dragging
2. Or click a **preset** → applies that temperature
3. **Neutral (6500K)** → apply an identity ramp. This does not restore a profile you had before Flux
4. **Turn Off** → remove Flux's ramp and restore the screen from before the first apply
5. **Close window (X)** → hides the window. The tint stays until you quit
6. **Tray menu** → Open (or double-click the tray icon), or Quit Flux v2 completely (the tint is removed)
7. **Launching again** while it's running → brings the existing window forward

The big number is the selected temperature, or **Off** when nothing is applied. Status is the claim about the screen: `Off`, `Setting 3000K…`, or `On: 3000K`. A failed apply does not say On.

### Via Flatpak

```bash
./build_flatpak.sh
flatpak install --user fluxv2_2.0.17.flatpak
```

Or install from a local build directory:

```bash
flatpak run --filesystem="$(pwd)" --share=network org.flatpak.Builder \
  --user --install --force-clean flatpak-build flatpak/io.github.Bistor1.FluxV2.yml
```

## Building

```bash
./build.sh
```

`build.sh` builds the `.deb` and, when Flatpak is available, the Flatpak bundle.

```bash
./build_deb.sh
```

Generates `fluxv2_2.0.17_amd64.deb`. `fpm` is used when it is installed; otherwise the script packs the archive with `ar` and `tar`.

## Technical Details

- **UI Framework**: CustomTkinter (dark mode) for main app; pure `tkinter` for the setup wizard (so it can install customtkinter itself)
- **Layout**: `main.py` is the entry point. The window, tray, color backends, and single-instance lock live in the `fluxv2` package
- **Backends**: `zwlr_gamma_control_v1` on wlroots, a KDE ICC profile with our VCGT on Plasma, XRandR gamma on X11. The connection stays open while a wlroots ramp is applied, because disconnecting restores the previous gamma
- **Icon Generation**: PIL — circular red → orange → blue gradient via `fluxv2/icon.py` (`gen_icon.py` writes it during the build)
- **Launcher**: Creates venv and installs `requirements.txt` if needed; falls back to the setup wizard for first run
- **Single-Instance**: flock plus a Unix socket at `~/.local/state/fluxv2/fluxv2.sock`; a second launch sends `SHOW` and exits
- **Logging**: `~/.local/state/fluxv2/fluxv2.log`

## License

MIT

---

**Flux v2** — clean, modern color temperature control.
