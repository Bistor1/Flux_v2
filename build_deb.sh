#!/bin/bash
# Flux v2 - .deb Package Builder (Fixed & Improved)
# Creates a clean, installable .deb package

set -euo pipefail

VERSION="2.0.8"
PACKAGE_NAME="fluxv2"
OUTPUT_FILE="${PACKAGE_NAME}_${VERSION}_amd64.deb"

echo "==> Baue Flux v2 Debian-Paket..."

# --- Pre-flight checks ---
if ! command -v fpm &>/dev/null; then
    echo "ERROR: fpm ist nicht installiert."
    echo "  sudo gem install fpm"
    exit 1
fi

if [ ! -f "main.py" ]; then
    echo "ERROR: main.py nicht gefunden. Skript im Projektverzeichnis ausführen."
    exit 1
fi

# --- Build directory with cleanup trap ---
BUILD_DIR=$(mktemp -d)
trap 'rm -rf "$BUILD_DIR"' EXIT

INSTALL_DIR="$BUILD_DIR/usr/share/fluxv2"
BIN_DIR="$BUILD_DIR/usr/bin"
DESKTOP_DIR="$BUILD_DIR/usr/share/applications"
ICON_DIR="$BUILD_DIR/usr/share/icons/hicolor/256x256/apps"

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$DESKTOP_DIR" "$ICON_DIR"

# --- App files ---
cp main.py "$INSTALL_DIR/"
cp first_run.py "$INSTALL_DIR/"
cp requirements.txt "$INSTALL_DIR/" 2>/dev/null || true

# Set correct permissions 
chmod 644 "$INSTALL_DIR/main.py"
chmod 644 "$INSTALL_DIR/first_run.py"
chmod 644 "$INSTALL_DIR/requirements.txt" 2>/dev/null || true

# --- Icon generieren (sun-to-moon transition, warm-to-cool, no purple) ---
if [ -f gen_icon.py ]; then
    python3 gen_icon.py "$ICON_DIR/fluxv2.png" 2>&1 || \
        echo "WARNING: Icon generation had issues"
else
    echo "WARNING: gen_icon.py missing — falling back to solid-color icon"
    python3 -c 'from PIL import Image; Image.new("RGBA",(256,256),(245,158,11,255)).save("'"$ICON_DIR/fluxv2.png"'","PNG")' 2>/dev/null || true
fi

# Always make sure we have SOME icon.
if [ ! -s "$ICON_DIR/fluxv2.png" ]; then
    python3 -c 'from PIL import Image; Image.new("RGBA",(256,256),(245,158,11,255)).save("'"$ICON_DIR/fluxv2.png"'","PNG")' 2>/dev/null || true
fi

# --- Desktop entry ---
cat > "$DESKTOP_DIR/fluxv2.desktop" <<EOF
[Desktop Entry]
Name=Flux v2
Comment=Modern color temperature control
Comment[de]=Moderne Farbtemperatur-Steuerung
Exec=fluxv2
Icon=fluxv2
Terminal=false
Type=Application
Categories=Utility;Settings;
StartupNotify=true
Keywords=color;temperature;redshift;screen;light;night;
EOF

# --- Robust launcher script ---
cat > "$BIN_DIR/fluxv2" <<'LAUNCHER'
#!/bin/bash
set -uo pipefail

APP_NAME="Flux v2"
APP_DIR="/usr/share/fluxv2"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/fluxv2"
VENV_DIR="$DATA_DIR/venv"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/fluxv2"
LOG_FILE="$STATE_DIR/fluxv2.log"

mkdir -p "$STATE_DIR" "$(dirname "$VENV_DIR")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

show_error() {
    local msg="$1"
    log "ERROR: $msg"
    if command -v zenity &>/dev/null; then
        zenity --error --title="$APP_NAME" --text="$msg" --width=450 2>/dev/null || true
    elif command -v notify-send &>/dev/null; then
        notify-send -u critical "$APP_NAME" "$msg" 2>/dev/null || true
    fi
    echo "$APP_NAME ERROR: $msg" >&2
    exit 1
}

log "=== $APP_NAME starting ==="

# --- System checks ---
if ! command -v redshift &>/dev/null; then
    show_error "Redshift is not installed.
Please run: sudo apt install redshift"
fi

if ! python3 -m venv --help &>/dev/null; then
    show_error "python3-venv is not installed.
Please run: sudo apt install python3-venv"
fi

# Check DISPLAY (graphical session)
if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    show_error "No display found. Flux v2 requires a graphical session."
fi

# --- Create / validate virtual environment ---
VENV_OK=0
if [ -x "$VENV_DIR/bin/python" ]; then
    if "$VENV_DIR/bin/python" -c "import customtkinter, PIL, tkinter, pystray" 2>/dev/null; then
        VENV_OK=1
    fi
fi

if [ "$VENV_OK" -eq 0 ]; then
    # First-time install (or venv broken) -> show the GUI setup wizard.
    # The wizard creates the venv and installs all required packages
    # (customtkinter, Pillow, pystray) with a live checklist.
    SETUPTMP="$(mktemp -d)"
    SETUP_ERR_FILE="$SETUPTMP/err"
    trap 'rm -rf "$SETUPTMP"' RETURN

    log "First-run setup wizard starting..."

    # Use the host python to run the wizard (it must not depend on the venv).
    export FLUXV2_VENV_DIR="$VENV_DIR"
    export FLUXV2_LOG_FILE="$LOG_FILE"
    export FLUXV2_SETUP_ERR="$SETUP_ERR_FILE"

    if ! python3 "$APP_DIR/first_run.py" 2>>"$LOG_FILE"; then
        if [ -s "$SETUP_ERR_FILE" ]; then
            ERR="$(cat "$SETUP_ERR_FILE")"
        else
            ERR="Setup failed. See: $LOG_FILE"
        fi
        rm -rf "$SETUPTMP"
        show_error "First-run setup failed:\n$ERR"
    fi
    rm -rf "$SETUPTMP"

    # Final verification (in case the wizard reported success but imports still fail)
    if ! "$VENV_DIR/bin/python" -c "import customtkinter, PIL, tkinter, pystray" 2>>"$LOG_FILE"; then
        show_error "Setup reported success but imports fail.
Try: rm -rf \"$VENV_DIR\" and restart Flux v2.
Check: $LOG_FILE"
    fi

    log "Virtual environment ready."
fi

# --- Launch ---
export PYTHONUNBUFFERED=1
log "Launching Python application..."
exec "$VENV_DIR/bin/python" "$APP_DIR/main.py" 2>>"$LOG_FILE"
LAUNCHER

chmod 755 "$BIN_DIR/fluxv2"

# --- Post-install script ---
POSTINST="$BUILD_DIR/postinst"
cat > "$POSTINST" <<'POSTINST'
#!/bin/bash
update-desktop-database -q /usr/share/applications 2>/dev/null || true
gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
POSTINST
chmod 755 "$POSTINST"

# --- Build .deb ---
echo "==> Baue Paket..."
rm -f "$OUTPUT_FILE"
fpm -s dir -t deb \
    -n "$PACKAGE_NAME" \
    -v "$VERSION" \
    -a amd64 \
    --description "Modern color temperature control with Redshift" \
    --license "MIT" \
    --depends "python3" \
    --depends "python3-venv" \
    --depends "python3-tk" \
    --depends "python3-pil" \
    --depends "redshift" \
    --depends "zenity" \
    --depends "python3-gi" \
    --depends "gir1.2-gtk-3.0" \
    --after-install "$POSTINST" \
    -C "$BUILD_DIR" \
    --exclude "postinst" \
    -p "$OUTPUT_FILE" \
    .

echo ""
echo "==> Fertig!"
echo "Paket: $OUTPUT_FILE"
echo ""
echo "Installation:"
echo "  sudo dpkg -i $OUTPUT_FILE"
echo "  sudo apt install -f"