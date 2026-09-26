"""
Typhoon datasets parser and storage (W-C0034-003, W-C0034-005).
Handles tropical cyclone tracks (JSON) and strike probability layers (KMZ).
"""

import io
import zipfile
from typing import Any, Dict, List, Optional
from src.database import get_db_connection

DATASET_TRACK_ID = "W-C0034-005"
DATASET_PROB_ID = "W-C0034-003"

def parse_typhoon_tracks(raw_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Parses W-C0034-005 JSON into structured typhoon track points (Analysis & Forecast).
    """
    records_node = raw_json.get("records", {})
    tc_container = records_node.get("TropicalCyclones", {})
    tc_list = tc_container.get("TropicalCyclone", []) if isinstance(tc_container, dict) else []

    if not isinstance(tc_list, list):
        tc_list = [tc_list] if tc_list else []

    track_points = []

    for tc in tc_list:
        ty_name = tc.get("TyphoonName", "UNKNOWN")
        cwa_name = tc.get("CwaTyphoonName", "")
        year = tc.get("Year")
        try:
            year = int(year) if year is not None else None
        except (ValueError, TypeError):
            year = None

        # 1. Parse AnalysisData (Historical & current fixes)
        analysis_fixes = tc.get("AnalysisData", {}).get("Fix", [])
        if not isinstance(analysis_fixes, list):
            analysis_fixes = [analysis_fixes] if analysis_fixes else []

        for fix in analysis_fixes:
            pt = _parse_fix_node(fix, ty_name, cwa_name, year, record_type="ANALYSIS")
            if pt:
                track_points.append(pt)

        # 2. Parse ForecastData (Predicted fixes)
        forecast_fixes = tc.get("ForecastData", {}).get("Fix", [])
        if not isinstance(forecast_fixes, list):
            forecast_fixes = [forecast_fixes] if forecast_fixes else []

        for fix in forecast_fixes:
            pt = _parse_fix_node(fix, ty_name, cwa_name, year, record_type="FORECAST")
            if pt:
                track_points.append(pt)

    return track_points

def _parse_fix_node(
    fix: Dict[str, Any],
    ty_name: str,
    cwa_name: str,
    year: Optional[int],
    record_type: str
) -> Optional[Dict[str, Any]]:
    """Helper to parse a single Fix node.
    Handles two CWA response formats:
      - Older: FixTime, Coordinate ("lat,lon")
      - Current: DateTime, CoordinateLatitude, CoordinateLongitude
    """
    # Time field: try DateTime first, then FixTime
    fix_time = fix.get("DateTime") or fix.get("FixTime")
    if not fix_time:
        return None

    # Coordinate fields: try separate lat/lon first, then combined string
    lat = _safe_float(fix.get("CoordinateLatitude"))
    lon = _safe_float(fix.get("CoordinateLongitude"))
    if lat is None or lon is None:
        coord_str = fix.get("Coordinate", "")
        if "," in coord_str:
            try:
                parts = coord_str.split(",")
                lat = float(parts[0].strip())
                lon = float(parts[1].strip())
            except (ValueError, IndexError):
                return None
        else:
            return None

    pressure = _safe_float(fix.get("Pressure"))
    max_wind = _safe_float(fix.get("MaxWindSpeed"))
    gust = _safe_float(fix.get("MaxGustSpeed") or fix.get("Gust"))

    return {
        "typhoon_name": ty_name,
        "cwa_name": cwa_name,
        "year": year,
        "record_type": record_type,
        "fix_time": fix_time,
        "latitude": lat,
        "longitude": lon,
        "pressure": pressure,
        "max_wind_speed": max_wind,
        "gust": gust
    }

def _safe_float(val: Any) -> Optional[float]:
    try:
        return float(val) if val not in (None, "-", "") else None
    except (ValueError, TypeError):
        return None

def save_typhoon_tracks(records: List[Dict[str, Any]]) -> int:
    """Inserts or updates typhoon tracks into SQLite."""
    if not records:
        return 0

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.executemany("""
            INSERT INTO typhoon_tracks (
                typhoon_name, cwa_name, year, record_type, fix_time,
                latitude, longitude, pressure, max_wind_speed, gust
            ) VALUES (
                :typhoon_name, :cwa_name, :year, :record_type, :fix_time,
                :latitude, :longitude, :pressure, :max_wind_speed, :gust
            )
            ON CONFLICT(typhoon_name, record_type, fix_time) DO UPDATE SET
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                pressure = excluded.pressure,
                max_wind_speed = excluded.max_wind_speed,
                gust = excluded.gust
        """, records)
        return len(records)

def parse_and_save_typhoon_probability_kmz(kmz_bytes: bytes, kmz_file_path: str) -> Dict[str, Any]:
    """
    Parses W-C0034-003 KMZ archive, extracts basic metadata, and records into typhoon_probabilities.
    """
    polygon_count = 0
    try:
        with zipfile.ZipFile(io.BytesIO(kmz_bytes)) as z:
            for name in z.namelist():
                if name.lower().endswith(".kml"):
                    kml_content = z.read(name).decode("utf-8", errors="ignore")
                    polygon_count = kml_content.count("<Polygon>")
                    break
    except Exception:
        pass

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO typhoon_probabilities (
                dataset_id, kmz_path, polygon_count
            ) VALUES (?, ?, ?)
        """, (DATASET_PROB_ID, str(kmz_file_path), polygon_count))
        return {
            "dataset_id": DATASET_PROB_ID,
            "kmz_path": str(kmz_file_path),
            "polygon_count": polygon_count
        }
