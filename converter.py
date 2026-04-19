"""
converter.py — NOTAM XLS → ED-318 GeoJSON core logic.

ED-318 is based on the ED-269 Chapter 8 data model used for UAS geographical
zones and U-space data exchange.
"""

import re
import math
import json
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Coordinate parsing
# ---------------------------------------------------------------------------

def dms_to_dd(dms_str):
    """
    Convert DDMMSS.SSN / DDDMMSS.SSE coordinate string to decimal degrees.
    Preserves leading zeros by operating on the raw string (not float→int).
    Returns float or None on failure.
    """
    dms_str = dms_str.strip()
    if not dms_str:
        return None
    hemi = dms_str[-1].upper()
    num = dms_str[:-1].strip()

    if "." in num:
        int_digits, dec_part = num.split(".", 1)
    else:
        int_digits, dec_part = num, "0"

    expected_len = 6 if hemi in ("N", "S") else 7
    int_digits = int_digits.zfill(expected_len)

    if len(int_digits) < expected_len:
        return None

    try:
        if hemi in ("N", "S"):
            deg = int(int_digits[0:2])
            mins = int(int_digits[2:4])
            secs = float(int_digits[4:6] + "." + dec_part)
        else:
            deg = int(int_digits[0:3])
            mins = int(int_digits[3:5])
            secs = float(int_digits[5:7] + "." + dec_part)
    except (ValueError, IndexError):
        return None

    dd = deg + mins / 60.0 + secs / 3600.0
    if hemi in ("S", "W"):
        dd = -dd
    return round(dd, 8)


def parse_coord_pair(lat_str, lon_str):
    """Parse a lat/lon DMS pair. Returns [lon, lat] or None."""
    lat = dms_to_dd(lat_str)
    lon = dms_to_dd(lon_str)
    if lat is None or lon is None:
        return None
    return [lon, lat]


def extract_coordinates(e_field_text):
    """
    Extract all [lon, lat] coordinate pairs from NOTAM E-field text.
    Handles multi-point polygons and single-point + radius patterns.
    """
    pattern = re.compile(
        r"(\d{6}(?:\.\d+)?[NS])\s*(\d{6,7}(?:\.\d+)?[EW])",
        re.IGNORECASE,
    )
    coords = []
    for lat_s, lon_s in pattern.findall(e_field_text):
        pair = parse_coord_pair(lat_s, lon_s)
        if pair:
            coords.append(pair)
    return coords


def q_line_center(q_line):
    """
    Extract centre point and radius from Q-line tail segment.
    Format: DDMMN DDDMME RRR
    Returns (lat_dd, lon_dd, radius_nm) or (None, None, None).
    """
    m = re.search(r"(\d{4}[NS])(\d{5}[EW])(\d{3})\s*$", q_line.strip())
    if not m:
        return None, None, None

    lat_dms, lon_dms = m.group(1), m.group(2)
    radius_nm = int(m.group(3))

    lat_dd = int(lat_dms[:2]) + int(lat_dms[2:4]) / 60.0
    if lat_dms[4] == "S":
        lat_dd = -lat_dd

    lon_dd = int(lon_dms[:3]) + int(lon_dms[3:5]) / 60.0
    if lon_dms[5] == "W":
        lon_dd = -lon_dd

    return round(lat_dd, 6), round(lon_dd, 6), radius_nm


def circle_to_polygon(lat, lon, radius_nm, num_points=64):
    """
    Approximate a circle as a GeoJSON Polygon ring.
    Uses spherical Earth approximation. num_points=64 gives a smooth circle.
    """
    radius_m = radius_nm * 1852.0
    R = 6371000.0
    lat_r = math.radians(lat)
    lon_r = math.radians(lon)
    d_r = radius_m / R
    ring = []
    for i in range(num_points + 1):
        bearing = math.radians(i * 360.0 / num_points)
        pt_lat = math.asin(
            math.sin(lat_r) * math.cos(d_r)
            + math.cos(lat_r) * math.sin(d_r) * math.cos(bearing)
        )
        pt_lon = lon_r + math.atan2(
            math.sin(bearing) * math.sin(d_r) * math.cos(lat_r),
            math.cos(d_r) - math.sin(lat_r) * math.sin(pt_lat),
        )
        ring.append([round(math.degrees(pt_lon), 8), round(math.degrees(pt_lat), 8)])
    return [ring]


def build_geometry(coords, q_line):
    """
    Build ED-318 horizontalProjection geometry.
      3+ coords  → Polygon
      1 coord + Q-line radius → circle Polygon
      fallback   → Q-line centre + radius circle, or Point
    """
    center_lat, center_lon, radius_nm = q_line_center(q_line)

    if len(coords) >= 3:
        ring = coords.copy()
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        return {"type": "Polygon", "coordinates": [ring]}

    if len(coords) == 1:
        if radius_nm and radius_nm > 0:
            return {"type": "Polygon", "coordinates": circle_to_polygon(coords[0][1], coords[0][0], radius_nm)}
        return {"type": "Point", "coordinates": coords[0]}

    if center_lat is not None:
        if radius_nm and radius_nm > 0:
            return {"type": "Polygon", "coordinates": circle_to_polygon(center_lat, center_lon, radius_nm)}
        return {"type": "Point", "coordinates": [center_lon, center_lat]}

    return None


# ---------------------------------------------------------------------------
# Altitude parsing
# ---------------------------------------------------------------------------

def parse_altitude(alt_str):
    """
    Parse F/G field altitude strings.
    Returns (metres: int, reference: str) where reference ∈ {AGL, AMSL, STD}.
    """
    s = (alt_str or "").strip().upper()
    if not s or s in ("SFC", "GND", "MSL"):
        return 0, "AGL"

    m = re.search(r"FL\s*(\d+)", s)
    if m:
        return int(m.group(1)) * 30, "STD"

    m = re.search(r"(\d+(?:\.\d+)?)\s*FT\s*(AGL|AMSL|MSL)?", s)
    if m:
        ref = (m.group(2) or "AGL").replace("MSL", "AMSL")
        return round(float(m.group(1)) * 0.3048), ref

    m = re.search(r"(\d+(?:\.\d+)?)\s*M\b", s)
    if m:
        return round(float(m.group(1))), "AGL"

    return 0, "AGL"


# ---------------------------------------------------------------------------
# Date / time parsing
# ---------------------------------------------------------------------------

def parse_notam_datetime(s):
    """YYMMDDHHMM → ISO 8601 UTC string, or None."""
    s = str(s).strip()
    if len(s) != 10:
        return None
    try:
        dt = datetime(
            year=2000 + int(s[0:2]),
            month=int(s[2:4]),
            day=int(s[4:6]),
            hour=int(s[6:8]),
            minute=int(s[8:10]),
            tzinfo=timezone.utc,
        )
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def parse_excel_date(s):
    """Parse '04/01/2026 0000' style strings from the Excel columns."""
    s = str(s).strip()
    for fmt in ("%m/%d/%Y %H%M", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue
    return s


# ---------------------------------------------------------------------------
# NOTAM field extraction helpers
# ---------------------------------------------------------------------------

QCODE_MAP = {
    "QWULW": ("COMMON", "REQ_AUTHORISATION"),
    "QRDCA": ("PROHIBITED", "PROHIBITED"),
    "QRRCA": ("RESTRICTED", "REQ_AUTHORISATION"),
    "QDUCA": ("DANGER", "CONDITIONAL"),
    "QRTCA": ("RESTRICTED", "REQ_AUTHORISATION"),
    "QWLAW": ("COMMON", "CONDITIONAL"),
}

ICAO_COUNTRY_MAP = {
    "WM": "MYS", "WS": "SGP", "VH": "AUS", "EG": "GBR",
    "ED": "DEU", "LF": "FRA", "EH": "NLD", "KZ": "USA",
    "K":  "USA", "CY": "CAN", "RJ": "JPN",
}


def _field(text, letter):
    m = re.search(rf"{letter}\)\s*(.*?)(?=\n[A-Z]\)|$)", text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _qcode_info(q_line):
    m = re.search(r"/([A-Z]{5})/", q_line)
    if m:
        code = m.group(1)
        if code in QCODE_MAP:
            return QCODE_MAP[code]
        if code.startswith("QR"):
            return "RESTRICTED", "REQ_AUTHORISATION"
        if code.startswith("QD"):
            return "DANGER", "CONDITIONAL"
        if code.startswith("QP"):
            return "PROHIBITED", "PROHIBITED"
    return "COMMON", "REQ_AUTHORISATION"


def _country(location):
    for prefix, cc in ICAO_COUNTRY_MAP.items():
        if location.startswith(prefix):
            return cc
    return "MYS"


def _schedule(d_field, b_dt, c_dt):
    if not d_field:
        return [{"startDateTime": b_dt, "endDateTime": c_dt}] if b_dt and c_dt else []
    daily = re.match(r"^(?:DLY\s+)?(\d{4})-(\d{4})$", d_field.strip())
    if daily:
        st, et = daily.group(1), daily.group(2)
        return [{
            "startDateTime": b_dt,
            "endDateTime": c_dt,
            "schedule": [{"day": ["MON","TUE","WED","THU","FRI","SAT","SUN"],
                          "startTime": f"{st[:2]}:{st[2:]}",
                          "endTime":   f"{et[:2]}:{et[2:]}"}],
        }]
    return [{"startDateTime": b_dt, "endDateTime": c_dt, "rawSchedule": d_field}]


# ---------------------------------------------------------------------------
# Core conversion
# ---------------------------------------------------------------------------

def notam_to_ed318_feature(row):
    """Convert one NOTAM dict/row to an ED-318 GeoJSON Feature dict."""
    full  = str(row.get("full_text", ""))
    num   = str(row.get("notam_num", "")).strip()
    loc   = str(row.get("location", "")).strip()

    q_line  = (_field(full, "Q") or (re.search(r"Q\)\s*(.+)", full) or type("",(),[("group",lambda s,i:"")])()).group(0)).strip()
    # robust Q-line extraction
    qm = re.search(r"Q\)\s*(.+)", full)
    q_line = qm.group(0).strip() if qm else ""

    e_field = _field(full, "E")
    f_text  = _field(full, "F") or "SFC"
    g_raw   = _field(full, "G")
    g_text  = re.sub(r"\s*F\).*$", "", g_raw, flags=re.DOTALL).strip()
    d_field = _field(full, "D") or None

    bm = re.search(r"B\)\s*(\d{10})", full)
    cm = re.search(r"C\)\s*(\d{10})", full)
    b_dt = parse_notam_datetime(bm.group(1)) if bm else parse_excel_date(row.get("effective_date", ""))
    c_dt = parse_notam_datetime(cm.group(1)) if cm else parse_excel_date(row.get("expiration_date", ""))

    zone_type, restriction = _qcode_info(q_line)
    name_m = re.search(r"\(([^)]+)\)", e_field)
    name   = name_m.group(1).strip() if name_m else num

    lower_val, lower_ref = parse_altitude(f_text)
    upper_val, upper_ref = parse_altitude(g_text)

    coords   = extract_coordinates(e_field)
    geometry = build_geometry(coords, q_line)
    applicability = _schedule(d_field, b_dt, c_dt)
    country  = _country(loc)

    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "identifier": num,
            "country": country,
            "name": name,
            "type": zone_type,
            "restriction": restriction,
            "restrictionConditions": "",
            "reason": ["AIR_TRAFFIC"],
            "otherReasonInfo": "",
            "regulationExemption": "YES" if ("SUBJ ATC" in e_field or "SUBJ TO ATC" in e_field) else "",
            "uSpaceClass": "",
            "message": e_field,
            "applicability": applicability,
            "zoneAuthority": [{
                "name": loc,
                "service": "INFORMATION",
                "email": "",
                "contactName": "",
                "siteURL": "",
                "phone": "",
                "purpose": "INFORMATION",
                "intervalBefore": "PT24H",
            }],
            "geometry": [{
                "uomDimensions": "M",
                "lowerLimit": lower_val,
                "lowerVerticalReference": lower_ref,
                "upperLimit": upper_val,
                "upperVerticalReference": upper_ref,
                "horizontalProjection": geometry,
            }] if geometry else [],
            "_source": {
                "notamNumber": num,
                "fir": loc,
                "issueDate": parse_excel_date(str(row.get("issue_date", ""))),
                "effectiveDate": b_dt,
                "expirationDate": c_dt,
                "classification": str(row.get("classification", "")),
                "rawQLine": q_line,
            },
        },
    }


def build_geojson(feature, notam_num, source_name=""):
    """Wrap a feature in an ED-318 FeatureCollection."""
    return {
        "type": "FeatureCollection",
        "features": [feature],
        "_metadata": {
            "standard": "ED-318",
            "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": source_name,
            "notamNumber": notam_num,
        },
    }


# ---------------------------------------------------------------------------
# Excel parsing
# ---------------------------------------------------------------------------

def parse_excel(path):
    """
    Read a FNS NOTAM Excel export and return a list of NOTAM dicts.
    Detects the header row automatically (looks for 'location' in first column).
    Returns all rows; filtering by class is left to the UI/caller.
    """
    suffix = Path(path).suffix.lower()
    engine = "xlrd" if suffix == ".xls" else "openpyxl"

    raw = pd.read_excel(path, engine=engine, header=None)

    # Find header row
    header_idx = None
    for i, row in raw.iterrows():
        if str(row.iloc[0]).strip().lower() == "location":
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Could not find header row with 'Location' in column A.")

    df = pd.read_excel(path, engine=engine, header=header_idx)
    df.columns = [
        "location", "notam_num", "classification",
        "issue_date", "effective_date", "expiration_date", "full_text",
    ]
    df = df.dropna(subset=["notam_num"])
    df = df[df["notam_num"].astype(str).str.strip() != ""]

    records = []
    for _, row in df.iterrows():
        records.append({
            "location":        str(row["location"]).strip(),
            "notam_num":       str(row["notam_num"]).strip(),
            "classification":  str(row["classification"]).strip(),
            "issue_date":      str(row["issue_date"]).strip(),
            "effective_date":  str(row["effective_date"]).strip(),
            "expiration_date": str(row["expiration_date"]).strip(),
            "full_text":       str(row["full_text"]).strip(),
        })
    return records


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    inp     = sys.argv[1] if len(sys.argv) > 1 else "notams.xls"
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("outputs")
    fclass  = sys.argv[3] if len(sys.argv) > 3 else "C"   # filter prefix, e.g. "C" or "all"

    out_dir.mkdir(parents=True, exist_ok=True)
    notams = parse_excel(inp)
    filtered = [n for n in notams if fclass == "all" or n["notam_num"].startswith(fclass)]
    print(f"{len(notams)} total NOTAMs, {len(filtered)} match class '{fclass}'")

    for row in filtered:
        try:
            feat = notam_to_ed318_feature(row)
            gj   = build_geojson(feat, row["notam_num"], inp)
            safe = row["notam_num"].replace("/", "_")
            out  = out_dir / f"{safe}.geojson"
            out.write_text(json.dumps(gj, indent=2))
            print(f"  ✓ {row['notam_num']} → {out.name}")
        except Exception as e:
            print(f"  ✗ {row['notam_num']}: {e}")
