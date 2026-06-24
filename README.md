[README.md](https://github.com/user-attachments/files/29289709/README.md)
# Flux v2

Modern screen color temperature control for Linux using Redshift.

A clean, modern GUI built with CustomTkinter featuring a dark theme and violet accents.

## Original author
The original autor of the unpublished v1 of Flux is: "CartoonRaccoon"

## Features

- **Modern UI**: Dark theme with violet accents (`#a855f7`), large temperature display, slider, and quick presets
- **Quick Presets**: From Daylight (6500K) to Deep Red (1800K) in a clean 2×4 grid
- **Redshift Integration**: Controls `redshift` via CLI (one-shot mode)
- **Actions**: Apply temperature, reset to normal, disable Redshift
- **System Tray** (planned)
- **Debian Package**: Complete `.deb` with launcher, automatic venv setup, and icon

## Requirements

- Linux (Debian/Ubuntu recommended)
- `redshift` installed
- Python 3.11+
- `python3-venv`, `python3-tk`, `python3-pil`

## Installation

### Via .deb package (recommended)

```bash
sudo dpkg -i fluxv2_2.0.1_amd64.deb
sudo apt install -f
```

Then launch `fluxv2` from the menu or terminal.

### From source (development)

```bash
cd Flux-v2
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Usage

1. Adjust temperature with the **slider** or click a **preset**
2. Click **Apply Temperature** to activate Redshift
3. **Reset to Normal** → restore default screen temperature
4. **Disable Redshift** → turn Redshift off completely

Status is shown live (Active / Reset / Disabled).

## Building the .deb package

```bash
./build_deb.sh
```

Generates `fluxv2_2.0.1_amd64.deb` (requires `fpm`).

## Technical Details

- **UI Framework**: CustomTkinter (dark mode)
- **Icon Generation**: PIL (violet theme)
- **Launcher**: Creates venv and installs dependencies automatically if needed
- **Logging**: `~/.local/state/fluxv2/fluxv2.log`

## License

MIT

---

**Flux v2** — clean, modern color temperature control.
