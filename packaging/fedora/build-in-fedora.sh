#!/bin/bash
# Run inside Fedora 43. Writes one RPM under /src/dist.
set -euo pipefail
dnf install -y rust cargo gcc pkgconf-pkg-config \
  wayland-devel lcms2-devel mesa-libGL-devel libxkbcommon-devel \
  rpm-build
cd /src/rust
cargo build --release --locked
echo "glibc symbols:"
objdump -T target/release/fluxv2-rs | grep -o 'GLIBC_[0-9.]*' | sort -Vu | tail -8
install -Dm755 target/release/fluxv2-rs /src/packaging/fedora/fluxv2-rs
install -Dm644 /src/LICENSE /src/packaging/fedora/LICENSE
mkdir -p /src/dist /tmp/fluxv2-rpm
rpmbuild -bb /src/packaging/fedora/fluxv2.spec \
  --define "_sourcedir /src/packaging/fedora" \
  --define "_rpmdir /src/dist" \
  --define "_topdir /tmp/fluxv2-rpm"
find /src/dist -name '*.rpm' -print
