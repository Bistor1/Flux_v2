# Flux v2

Modern screen color temperature control for Linux using Redshift.

A clean, modern GUI built with CustomTkinter — dark theme, live slider, and a temperature-gradient logo.

## Original author
The original author of the unpublished v1 of Flux is: "CartoonRacoon"

## Features

- **Modern UI**: Dark theme with warm-to-cool gradient accents (orange `#f59e0b` ↔ blue `#3b82f6`), large temperature display that recolors with the temperature
- **Live Slider**: Moving the slider applies the temperature automatically (~350 ms after you stop dragging) — no need to click Apply
- **Quick Presets**: From Daylight (6500K) to Deep Red (1800K) in a clean 2×4 grid
- **Color temperature**: On KDE, talks to KWin Night Light over D-Bus (no extra package). Elsewhere controls `redshift` or `gammastep` via CLI (one-shot mode)
- **Actions**: Reset to normal, disable Redshift
- **System Tray**: Minimizes to tray on window close; tray menu offers **Open** and **Quit** (requires `pystray`, auto-installed on first run)
- **Single Instance**: Launching a second time brings the running window to the front instead of creating a duplicate tray icon
- **First-Run Setup Wizard**: On first launch, Flux v2 opens a pure-tkinter window with a live checklist that creates the venv and installs all required Python packages (customtkinter, Pillow, pystray) step-by-step
- **Logo**: Single circular temperature-gradient icon (warm red center → orange → cool blue rim)
- **Debian Package**: Complete `.deb` with launcher, automatic setup, and icon

## Requirements

- Linux (Debian/Ubuntu recommended)
- KDE Plasma (KWin Night Light), or `redshift` / `gammastep` installed
- Python 3.11+
- `python3-venv`, `python3-tk`, `python3-pil`, `python3-gi`, `gir1.2-gtk-3.0`

## Installation

### Via .deb package (recommended)

```bash
sudo dpkg -i fluxv2_2.0.9_amd64.deb
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

## Usage

1. Drag the **slider** → temperature applies automatically when you release it
2. Or click a **preset** → applies that temperature instantly
3. **Reset to Normal** → restore default screen temperature
4. **Disable Redshift** → turn Redshift off completely
5. **Close window (X)** → minimizes to the system tray
6. **Tray menu** → Open (or double-click the tray icon), or Quit Flux v2 completely
7. **Launching again** while it's running → brings the existing window forward

Status is shown live (Active / Reset / Disabled). The temperature display recolors from warm (low K) to cool (high K).

## Building the .deb package

```bash
./build_deb.sh
```

Generates `fluxv2_2.0.9_amd64.deb` (requires `fpm`).

## Technical Details

- **UI Framework**: CustomTkinter (dark mode) for main app; pure `tkinter` for the setup wizard (so it can install customtkinter itself)
- **Icon Generation**: PIL — circular red → orange → blue gradient via `gen_icon.py`
- **Launcher**: Creates venv and installs dependencies automatically if needed; falls back to setup wizard for first run
- **Single-Instance**: Unix-domain IPC socket at `~/.local/state/fluxv2/fluxv2.sock`; second launch sends `SHOW` to the existing instance and exits
- **Logging**: `~/.local/state/fluxv2/fluxv2.log`

## License

MIT

---

**Flux v2** — clean, modern color temperature control.
