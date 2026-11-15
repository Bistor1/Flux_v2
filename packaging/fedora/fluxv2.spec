Name:           fluxv2
Version:        2.0.20
Release:        1.fc43
Summary:        Screen color temperature control
License:        MIT
URL:            https://github.com/Bistor1/Flux_v2
ExclusiveArch:  x86_64

Source0:        fluxv2-rs
Source1:        fluxv2.desktop
Source2:        fluxv2.png
Source3:        LICENSE

# Loaded with dlopen, so they are not in the ELF NEEDED list.
Requires:       wayland
Requires:       lcms2
Requires:       mesa-libGL
Requires:       libxkbcommon

%global debug_package %{nil}
%global _build_id_links none

%description
Flux v2 changes the color temperature of the screen. It talks to KDE Plasma
and wlroots directly. GNOME Wayland is not supported.

This package was built on Fedora 43. It installs on Fedora 43 and newer.

%prep
%setup -q -c -T

%build

%install
install -Dm755 %{SOURCE0} %{buildroot}%{_bindir}/fluxv2
install -Dm644 %{SOURCE1} %{buildroot}%{_datadir}/applications/fluxv2.desktop
install -Dm644 %{SOURCE2} %{buildroot}%{_datadir}/icons/hicolor/256x256/apps/fluxv2.png
install -Dm644 %{SOURCE3} %{buildroot}%{_datadir}/licenses/%{name}/LICENSE

%files
%{_bindir}/fluxv2
%{_datadir}/applications/fluxv2.desktop
%{_datadir}/icons/hicolor/256x256/apps/fluxv2.png
%{_datadir}/licenses/%{name}/LICENSE
