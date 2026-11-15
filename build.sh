#!/bin/bash
# Build the .deb and the Flatpak bundle.
set -euo pipefail
cd "$(dirname "$0")"

./build_deb.sh
if command -v makepkg >/dev/null; then
    ./build_arch.sh
else
    echo "==> makepkg is not installed, skipping the Arch package"
fi
if command -v flatpak >/dev/null; then
    ./build_flatpak.sh
else
    echo "==> flatpak is not installed, skipping the Flatpak bundle"
fi
