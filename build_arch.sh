#!/bin/bash
# Build an Arch package of Flux v2 with makepkg.
# CustomTkinter is not in the official repositories, so it is vendored.
set -euo pipefail

cd "$(dirname "$0")"

VERSION="2.0.19"
PKGREL="1"
OUTPUT="fluxv2-${VERSION}-${PKGREL}-any.pkg.tar.zst"

if [ ! -f main.py ]; then
  echo "ERROR: main.py nicht gefunden. Skript im Projektverzeichnis ausführen."
  exit 1
fi
if ! grep -q "__version__ = \"${VERSION}\"" fluxv2/__init__.py; then
  echo "ERROR: fluxv2/__init__.py is not ${VERSION}."
  exit 1
fi
if ! command -v makepkg >/dev/null; then
  echo "ERROR: makepkg is not installed."
  exit 1
fi

if python3 -m pip --version >/dev/null 2>&1; then
  PIP=(python3 -m pip)
elif [ -x .venv/bin/python ]; then
  PIP=(.venv/bin/python -m pip)
else
  echo "ERROR: python-pip is required to vendor CustomTkinter."
  exit 1
fi

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

echo "==> Baue Flux v2 Arch-Paket ${VERSION}..."
mkdir -p "$WORK/vendor" "$WORK/fluxv2"
cp main.py first_run.py requirements.txt LICENSE "$WORK/"
cp fluxv2/*.py "$WORK/fluxv2/"
cp packaging/arch/PKGBUILD "$WORK/PKGBUILD"
if ! grep -q "pkgver=${VERSION}" "$WORK/PKGBUILD"; then
  echo "ERROR: packaging/arch/PKGBUILD is not pkgver=${VERSION}."
  exit 1
fi

python3 gen_icon.py "$WORK/fluxv2.png" || \
  python3 -c 'from PIL import Image; Image.new("RGBA",(256,256),(245,158,11,255)).save("'"$WORK/fluxv2.png"'","PNG")'

echo "==> Vendor CustomTkinter..."
"${PIP[@]}" install --disable-pip-version-check --target "$WORK/vendor" 'customtkinter>=5.2.0'

# Runtime libraries are package dependencies. They do not have to be installed
# on the build machine for the archive itself to be produced.
(
  cd "$WORK"
  makepkg -f --noconfirm --nodeps --skipinteg
)

cp "$WORK/$OUTPUT" "./$OUTPUT"
echo ""
echo "==> Fertig!"
echo "Paket: $OUTPUT"
echo ""
echo "Installation:"
echo "  sudo pacman -U $OUTPUT"
