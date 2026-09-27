"""Parsing and persistence for CWA felt-earthquake reports."""
import hashlib
import json
from typing import Any, Dict, List, Optional

from src.database import get_db_connection

EARTHQUAKE_DATASETS = {
    "E-A0015-001": "顯著有感地震",
    "E-A0016-001": "小區域有感地震",
}


def _get(node: Any, *names: str) -> Any:
    """Get a field without assuming the API's casing or wrapper version."""
    if not isinstance(node, dict):
        return None
    wanted = {name.casefold() for name in names}
    return next((value for key, value in node.items() if str(key).casefold() in wanted), None)


def _items(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _float(value: Any) -> Optional[float]:
    if value is None or str(value).strip() in {"", "-", "-99", "-999", "X", "None"}:
        return None
    try:
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError):
        return None


def _walk_for_key(node: Any, *names: str) -> Any:
    value = _get(node, *names)
    if value is not None:
        return value
    if isinstance(node, dict):
        for child in node.values():
            found = _walk_for_key(child, *names)
            if found is not None:
                return found
    elif isinstance(node, list):
        for child in node:
            found = _walk_for_key(child, *names)
            if found is not None:
                return found
    return None


def _intensity_summary(intensity: Any) -> tuple[Optional[str], List[Dict[str, Any]]]:
    """Summarize reported shaking areas while retaining their source fields."""
    areas = _items(_get(intensity, "ShakingArea", "ShakingAreas", "Area"))
    # Some API versions put the areas one level below Intensity or use a wrapper.
    if not areas:
        areas = _items(_walk_for_key(intensity, "ShakingArea", "ShakingAreas"))
    labels: List[str] = []
    for area in areas:
        label = _get(area, "AreaDesc", "CountyName", "Location", "AreaName", "StationName", "TownName")
        level = _get(area, "AreaIntensity", "StationIntensity", "Intensity", "IntensityLevel", "MagnitudeValue")
        if isinstance(level, dict):
            level = _get(level, "Value", "Name", "Description", "Intensity")
        if label or level:
            labels.append(" ".join(str(part) for part in (label, level) if part not in (None, "")))
    max_level = _walk_for_key(intensity, "MaximumIntensity", "MaxIntensity", "MaxIntensityLevel")
    if isinstance(max_level, dict):
        max_level = _get(max_level, "Value", "Name", "Description", "Intensity")
    summary = str(max_level) if max_level not in (None, "") else None
    if not summary and labels:
        summary = labels[0].split()[-1]
    return summary, areas


def parse_earthquake_reports(raw_json: Dict[str, Any], dataset_id: str) -> List[Dict[str, Any]]:
    """Parse both CWA report datasets and tolerate singleton/case variants."""
    if dataset_id not in EARTHQUAKE_DATASETS:
        raise ValueError(f"不支援的地震資料集：{dataset_id}")
    records = _get(raw_json, "records", "Records") or {}
    reports = _items(_get(records, "Earthquake", "Earthquakes"))
    parsed: List[Dict[str, Any]] = []
    for report in reports:
        info = _get(report, "EarthquakeInfo", "EarthquakeInformation") or {}
        epicenter = _get(info, "Epicenter") or _walk_for_key(info, "Epicenter") or {}
        magnitude_node = _get(info, "EarthquakeMagnitude", "Magnitude") or _walk_for_key(info, "EarthquakeMagnitude", "Magnitude") or {}
        magnitude = _get(magnitude_node, "MagnitudeValue", "Value")
        if magnitude is None and not isinstance(magnitude_node, dict):
            magnitude = magnitude_node
        intensity = _get(report, "Intensity") or {}
        max_intensity, areas = _intensity_summary(intensity)
        origin_time = (
            _get(info, "OriginTime", "OriginDateTime")
            or _walk_for_key(info, "OriginTime", "OriginDateTime")
            or _get(report, "OriginTime")
        )
        location = (
            _get(info, "EpicenterLocation", "Location")
            or _get(epicenter, "Location", "EpicenterLocation")
        )
        if not origin_time:
            continue
        event_no = _get(report, "EarthquakeNo", "EarthquakeNumber", "EventNo") or _get(info, "EarthquakeNo")
        report_no = _get(report, "ReportNo") or ""
        # Stable key even when a data version omits the report/earthquake number.
        identity = "|".join(str(part or "") for part in (event_no, origin_time, location, magnitude, report_no))
        event_key = hashlib.sha256(f"{dataset_id}|{identity}".encode("utf-8")).hexdigest()
        parsed.append({
            "dataset_id": dataset_id,
            "event_key": event_key,
            "earthquake_no": str(event_no) if event_no is not None else None,
            "report_no": str(report_no),
            "origin_time": str(origin_time),
            "report_time": _get(report, "ReportTime", "IssueTime"),
            "epicenter_location": str(location) if location else None,
            "epicenter_lat": _float(_get(epicenter, "EpicenterLatitude", "Latitude") or _walk_for_key(info, "EpicenterLatitude")),
            "epicenter_lon": _float(_get(epicenter, "EpicenterLongitude", "Longitude") or _walk_for_key(info, "EpicenterLongitude")),
            "focal_depth": _float(_get(info, "FocalDepth", "Depth") or _walk_for_key(info, "FocalDepth", "Depth")),
            "magnitude": _float(magnitude),
            "max_intensity": max_intensity,
            "report_content": _get(report, "ReportContent", "Content"),
            "report_image_uri": _get(report, "ReportImageURI", "ImageURI"),
            "web_url": _get(report, "Web", "WebURL", "ReportURL"),
            "intensity_json": json.dumps(areas, ensure_ascii=False),
        })
    return parsed


def save_earthquake_reports(records: List[Dict[str, Any]]) -> int:
    if not records:
        return 0
    with get_db_connection() as conn:
        conn.executemany(
            """INSERT INTO earthquake_events
               (dataset_id,event_key,earthquake_no,report_no,origin_time,report_time,epicenter_location,
                epicenter_lat,epicenter_lon,focal_depth,magnitude,max_intensity,report_content,
                report_image_uri,web_url,intensity_json)
               VALUES (:dataset_id,:event_key,:earthquake_no,:report_no,:origin_time,:report_time,:epicenter_location,
                :epicenter_lat,:epicenter_lon,:focal_depth,:magnitude,:max_intensity,:report_content,
                :report_image_uri,:web_url,:intensity_json)
               ON CONFLICT(dataset_id,event_key) DO UPDATE SET
                earthquake_no=excluded.earthquake_no,report_no=excluded.report_no,report_time=excluded.report_time,
                epicenter_location=excluded.epicenter_location,epicenter_lat=excluded.epicenter_lat,
                epicenter_lon=excluded.epicenter_lon,focal_depth=excluded.focal_depth,magnitude=excluded.magnitude,
                max_intensity=excluded.max_intensity,report_content=excluded.report_content,
                report_image_uri=excluded.report_image_uri,web_url=excluded.web_url,
                intensity_json=excluded.intensity_json,updated_at=CURRENT_TIMESTAMP""",
            records,
        )
    return len(records)
