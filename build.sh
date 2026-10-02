#!/bin/bash
# Build the .deb and the Flatpak bundle.
set -euo pipefail
cd "$(dirname "$0")"

./build_deb.sh
./build_flatpak.sh
