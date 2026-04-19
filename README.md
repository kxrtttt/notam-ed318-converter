# NOTAM → ED-318 GeoJSON Converter

A web application that converts FAA/FNS NOTAM Excel exports into
**ED-318** (EUROCAE) compliant GeoJSON files, with an OpenStreetMap-based
polygon preview and one-click download.

---

## Features

- Upload `.xls` / `.xlsx` NOTAM exports from the FAA FNS portal
- Filter by NOTAM class (All / C-class / A-class)
- Preview each NOTAM's airspace polygon on an **OpenStreetMap** map (via Leaflet)
- Inspect the full ED-318 GeoJSON structure in the right panel
- Download individual ED-318 compliant GeoJSON files per NOTAM

## ED-318 Fields Mapped

| ED-318 Field | Source |
|---|---|
| `identifier` | NOTAM number |
| `country` | Derived from FIR/ICAO prefix |
| `name` | Bracketed name in E-field |
| `type` / `restriction` | Q-line NOTAM code (QWULW, QRDCA, etc.) |
| `message` | Full E-field text |
| `applicability` | B/C/D fields → startDateTime / endDateTime / schedule |
| `geometry[].lowerLimit` | F-field (e.g. SFC → 0m AGL) |
| `geometry[].upperLimit` | G-field (e.g. 400FT AGL → 122m) |
| `horizontalProjection` | GeoJSON Polygon from parsed E-field coordinates |

Geometry is built from:
- **3+ coordinate points** in the E-field → closed Polygon
- **1 coordinate + Q-line radius** → circle approximated as Polygon (64 pts)
- **Q-line centre + radius only** → fallback circle Polygon

---

## Getting Started

### Requirements

- Python 3.9+
- pip

### Install

```bash
git clone https://github.com/your-org/notam-ed318-converter.git
cd notam-ed318-converter
pip install -r requirements.txt
```

### Run

```bash
python app.py
```

Then open [http://localhost:5000](http://localhost:5000) in your browser.

---

## CLI Usage

You can also run the converter from the command line without the web UI:

```bash
python converter.py input.xls outputs/ C
```

Arguments:
1. `input.xls` — path to the FNS NOTAM Excel export
2. `outputs/` — directory to write GeoJSON files into
3. `C` — NOTAM class prefix to filter on (`C`, `A`, or `all`)

---

## Project Structure

```
notam-ed318-converter/
├── app.py            # Flask web server
├── converter.py      # Core conversion logic (also runnable as CLI)
├── requirements.txt
├── outputs/          # Generated GeoJSON files (git-ignored)
└── templates/
    └── index.html    # Single-page UI (Leaflet + OSM)
```

---

## Input File Format

The app expects the standard **FAA FNS NOTAM export** `.xls` format:

- Row 1–3: metadata / query info (auto-skipped)
- Row 5: header — `Location | NOTAM Number | Classification | Issue Date | Effective Date | Expiration Date | Full Text`
- Row 6+: NOTAM data rows

---

## Supported NOTAM Q-Codes → ED-318 Types

| Q-Code | ED-318 Type | Restriction |
|---|---|---|
| QWULW | COMMON | REQ_AUTHORISATION |
| QRDCA | PROHIBITED | PROHIBITED |
| QRRCA | RESTRICTED | REQ_AUTHORISATION |
| QDUCA | DANGER | CONDITIONAL |
| QRTCA | RESTRICTED | REQ_AUTHORISATION |
| QWLAW | COMMON | CONDITIONAL |

---

## License

MIT
