# Flux v2

**Moderne Farbtemperatur-Steuerung** für Linux mit Redshift.

Eine schlanke, moderne GUI-App zum Steuern der Bildschirm-Farbtemperatur – gebaut mit CustomTkinter und einem sauberen Dark-Design mit violetten Akzenten.

## Features

- **Moderne UI**: Dark-Theme mit violetten Akzenten (#a855f7), große Temperatur-Anzeige, Slider und Presets
- **Schnelle Presets**: Daylight (6500K) bis Deep Red (1800K) in einem übersichtlichen 2×4-Grid
- **Redshift-Integration**: Steuert `redshift` über CLI (One-Shot-Modus)
- **Aktionen**: Temperatur anwenden, auf Normal zurücksetzen, Redshift deaktivieren
- **System-Tray** (geplant / in Entwicklung)
- **Debian-Paket**: Vollständiges `.deb`-Paket mit Launcher, venv-Setup und Icon

## Voraussetzungen

- Linux (Debian/Ubuntu-basiert empfohlen)
- `redshift` installiert
- Python 3.11+
- `python3-venv`, `python3-tk`, `python3-pil`

## Installation

### Über .deb-Paket (empfohlen)

```bash
sudo dpkg -i fluxv2_2.0.1_amd64.deb
sudo apt install -f
```

Danach einfach `fluxv2` im Terminal oder über das Anwendungsmenü starten.

### Aus dem Source (für Entwicklung)

```bash
git clone ...
cd Flux-v2
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Bedienung

1. Temperatur per **Slider** einstellen oder **Preset** anklicken
2. **Apply Temperature** drücken → Redshift wird aktiviert
3. **Reset to Normal** → zurück auf normale Farbtemperatur
4. **Disable Redshift** → Redshift komplett deaktivieren

Die App zeigt den aktuellen Status an (Active / Reset / Disabled).

## Bauen des .deb-Pakets

```bash
./build_deb.sh
```

Erzeugt `fluxv2_2.0.1_amd64.deb` (benötigt `fpm`).

## Technische Details

- **UI-Framework**: CustomTkinter (Dark Mode)
- **Icon-Generierung**: PIL (violet-themed)
- **Launcher**: Erstellt bei Bedarf eine venv und installiert Abhängigkeiten automatisch
- **Logging**: `~/.local/state/fluxv2/fluxv2.log`

## Lizenz

MIT

---

**Flux v2** – saubere, moderne Farbtemperatur-Steuerung ohne Schnickschnack.