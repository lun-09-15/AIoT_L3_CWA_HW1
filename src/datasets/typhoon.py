"""
Typhoon track and strike-probability (KMZ/KML) parsing and persistence.
"""
from datetime import datetime, timedelta
from io import BytesIO
import re
import zipfile
from typing import Any, Dict, List, Optional
import xml.etree.ElementTree as ET

from src.database import get_db_connection

DATASET_TRACK_ID = "W-C0034-005"
DATASET_PROB_ID = "W-C0034-003"


def _items(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict): return [value]
    if isinstance(value, list): return [item for item in value if isinstance(item, dict)]
    return []


def parse_typhoon_tracks(raw_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize analysis and forecast fixes from the CWA tropical-cyclone feed."""
    records = raw_json.get("records", {}) if isinstance(raw_json, dict) else {}
    container = records.get("TropicalCyclones", {}) if isinstance(records, dict) else {}
    cyclones = _items(container.get("TropicalCyclone")) if isinstance(container, dict) else _items(container)
    points: List[Dict[str, Any]] = []
    for cyclone in cyclones:
        name = str(cyclone.get("TyphoonName") or cyclone.get("Name") or "Unknown")
        cwa_name = str(cyclone.get("CwaTyphoonName") or "")
        try: year = int(cyclone.get("Year"))
        except (TypeError, ValueError): year = None
        for block, kind in ((cyclone.get("AnalysisData"), "ANALYSIS"), (cyclone.get("ForecastData"), "FORECAST")):
            fixes = _items(block.get("Fix")) if isinstance(block, dict) else []
            for fix in fixes:
                point = _parse_fix_node(fix, name, cwa_name, year, kind)
                if point:
                    points.append(point)
    return points


def _parse_fix_node(fix: Dict[str, Any], name: str, cwa_name: str, year: Optional[int], kind: str) -> Optional[Dict[str, Any]]:
    time_value = fix.get("DateTime") or fix.get("FixTime")
    if kind == "FORECAST" and fix.get("InitialTime"):
        try:
            initial = datetime.fromisoformat(str(fix["InitialTime"]).replace("Z", "+00:00"))
            time_value = (initial + timedelta(hours=int(fix.get("ForecastHour", 0)))).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            time_value = time_value or fix.get("InitialTime")
    lat, lon = _safe_float(fix.get("CoordinateLatitude")), _safe_float(fix.get("CoordinateLongitude"))
    if (lat is None or lon is None) and fix.get("Coordinate"):
        try: lat, lon = (float(part.strip()) for part in str(fix["Coordinate"]).split(",", 1))
        except (TypeError, ValueError): return None
    if not time_value or lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None
    return {
        "typhoon_name": name, "cwa_name": cwa_name, "year": year,
        "record_type": kind, "fix_time": str(time_value), "latitude": lat, "longitude": lon,
        "pressure": _safe_float(fix.get("Pressure")),
        "max_wind_speed": _safe_float(fix.get("MaxWindSpeed")),
        "gust": _safe_float(fix.get("MaxGustSpeed") or fix.get("Gust")),
    }


def _safe_float(value: Any) -> Optional[float]:
    if value is None or str(value).strip() in {"", "-", "-99", "-999", "X"}: return None
    try:
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError): return None


def save_typhoon_tracks(records: List[Dict[str, Any]]) -> int:
    """Replace the current track feed so storms removed by CWA do not remain active."""
    with get_db_connection() as conn:
        conn.execute("DELETE FROM typhoon_tracks")
        if records:
            conn.executemany(
                """INSERT INTO typhoon_tracks
                   (typhoon_name,cwa_name,year,record_type,fix_time,latitude,longitude,pressure,max_wind_speed,gust)
                   VALUES (:typhoon_name,:cwa_name,:year,:record_type,:fix_time,:latitude,:longitude,:pressure,:max_wind_speed,:gust)""",
                records,
            )
    return len(records)


def _local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _descendant_text(element: ET.Element, name: str) -> Optional[str]:
    for child in element.iter():
        if _local_name(child) == name and child.text and child.text.strip(): return child.text.strip()
    return None


def _coordinate_rings(polygon: ET.Element) -> List[List[List[float]]]:
    rings: List[List[List[float]]] = []
    for child in polygon.iter():
        if _local_name(child) != "coordinates" or not child.text: continue
        points: List[List[float]] = []
        for raw in child.text.split():
            parts = raw.split(",")
            if len(parts) >= 2:
                try: points.append([float(parts[0]), float(parts[1])])
                except ValueError: continue
        if len(points) >= 4: rings.append(points)
    return rings


def parse_typhoon_probability_kmz(kmz_bytes: bytes) -> Dict[str, Any]:
    """Convert CWA KMZ KML polygons into GeoJSON features for Folium."""
    with zipfile.ZipFile(BytesIO(kmz_bytes)) as archive:
        kml_name = next((name for name in archive.namelist() if name.lower().endswith(".kml")), None)
        if not kml_name: raise ValueError("KMZ 中找不到 KML 圖層。")
        root = ET.fromstring(archive.read(kml_name))
    features: List[Dict[str, Any]] = []
    product_time = None
    for element in root.iter():
        if _local_name(element) == "description" and element.text:
            match = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2})?", element.text.strip())
            if match: product_time = match.group(0); break
    for placemark in (el for el in root.iter() if _local_name(el) == "Placemark"):
        label = _descendant_text(placemark, "name") or ""
        style = _descendant_text(placemark, "styleUrl") or ""
        for polygon in (el for el in placemark.iter() if _local_name(el) == "Polygon"):
            rings = _coordinate_rings(polygon)
            if rings:
                features.append({"type": "Feature", "properties": {"probability": label.strip(), "style": style},
                                 "geometry": {"type": "Polygon", "coordinates": rings}})
    return {"type": "FeatureCollection", "features": features, "product_time": product_time, "polygon_count": len(features)}


def save_typhoon_probability(kmz_path: str, polygon_count: int, product_time: Optional[str], content_sha256: Optional[str] = None) -> int:
    with get_db_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO typhoon_probabilities(dataset_id,product_time,kmz_path,polygon_count,content_sha256)
               VALUES(?,?,?,?,?)""",
            (DATASET_PROB_ID, product_time, kmz_path, polygon_count, content_sha256),
        )
        return int(cursor.lastrowid)


def parse_and_save_typhoon_probability_kmz(kmz_bytes: bytes, kmz_file_path: str) -> Dict[str, Any]:
    parsed = parse_typhoon_probability_kmz(kmz_bytes)
    save_typhoon_probability(kmz_file_path, parsed["polygon_count"], parsed["product_time"])
    return {"dataset_id": DATASET_PROB_ID, "kmz_path": kmz_file_path, **parsed}
