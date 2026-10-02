#!/bin/bash
# Build a single-file Flatpak bundle of Flux v2.
set -euo pipefail

cd "$(dirname "$0")"

VERSION="2.0.10"
APP_ID="io.github.Bistor1.FluxV2"
MANIFEST="flatpak/io.github.Bistor1.FluxV2.yml"
BUILD_DIR="flatpak-build"
REPO_DIR="flatpak-repo"
BUNDLE="fluxv2_${VERSION}.flatpak"

if ! command -v flatpak >/dev/null; then
    echo "ERROR: flatpak is not installed."
    exit 1
fi

echo "==> Baue Flatpak ${APP_ID} ${VERSION}..."
rm -rf "$BUILD_DIR" "$REPO_DIR"

if command -v flatpak-builder >/dev/null; then
    flatpak-builder --user --force-clean --repo="$REPO_DIR" "$BUILD_DIR" "$MANIFEST"
else
    flatpak run --filesystem="$(pwd)" --share=network org.flatpak.Builder \
        --user --force-clean --repo="$REPO_DIR" "$BUILD_DIR" "$MANIFEST"
fi

echo "==> Exportiere Bundle..."
flatpak build-bundle "$REPO_DIR" "$BUNDLE" "$APP_ID"

echo ""
echo "==> Fertig!"
echo "Bundle: $BUNDLE"
echo ""
echo "Installation:"
echo "  flatpak install --user $BUNDLE"
