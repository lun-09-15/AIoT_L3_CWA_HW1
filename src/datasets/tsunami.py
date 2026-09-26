"""Parser and SQLite persistence for CWA tsunami notices."""
from typing import Any, Dict, List, Optional
from src.database import get_db_connection

DATASET_ID = "E-A0014-001"


def _float(value: Any) -> Optional[float]:
    if value is None or str(value).strip() in {"", "-", "-99", "-999", "X"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _items(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def parse_tsunami(raw_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Accept the current REST response records.Tsunami shape and singleton objects."""
    records = _dict(raw_json.get("records"))
    reports = _items(records.get("Tsunami"))
    parsed: List[Dict[str, Any]] = []
    for item in reports:
        earthquake = _dict(item.get("EarthquakeInfo"))
        epicenter = _dict(earthquake.get("Epicenter"))
        magnitude = _dict(earthquake.get("EarthquakeMagnitude"))
        valid_time = _dict(item.get("ValidTime"))
        issue_time = item.get("IssueTime")
        if not issue_time:
            continue
        tsunami_no = item.get("TsunamiNo")
        try:
            tsunami_no = int(tsunami_no) if tsunami_no is not None else None
        except (TypeError, ValueError):
            tsunami_no = None
        parsed.append({
            "tsunami_no": tsunami_no,
            "report_no": str(item.get("ReportNo") or ""),
            "issue_time": str(issue_time),
            "valid_end_time": valid_time.get("EndTime"),
            "report_color": item.get("ReportColor"),
            "report_type": item.get("ReportType"),
            "report_content": item.get("ReportContent"),
            "origin_time": earthquake.get("OriginTime"),
            "epicenter_location": epicenter.get("Location"),
            "epicenter_lat": _float(epicenter.get("EpicenterLatitude")),
            "epicenter_lon": _float(epicenter.get("EpicenterLongitude")),
            "focal_depth": _float(earthquake.get("FocalDepth")),
            "magnitude": _float(magnitude.get("MagnitudeValue")),
            "web_url": item.get("Web"),
        })
    return parsed


def save_tsunami_events(records: List[Dict[str, Any]]) -> int:
    if not records:
        return 0
    with get_db_connection() as conn:
        conn.executemany(
            """INSERT INTO tsunami_events
               (tsunami_no,report_no,issue_time,valid_end_time,report_color,report_type,report_content,
                origin_time,epicenter_location,epicenter_lat,epicenter_lon,focal_depth,magnitude,web_url)
               VALUES (:tsunami_no,:report_no,:issue_time,:valid_end_time,:report_color,:report_type,:report_content,
                :origin_time,:epicenter_location,:epicenter_lat,:epicenter_lon,:focal_depth,:magnitude,:web_url)
               ON CONFLICT(tsunami_no,report_no,issue_time) DO UPDATE SET
                valid_end_time=excluded.valid_end_time, report_color=excluded.report_color,
                report_type=excluded.report_type, report_content=excluded.report_content,
                origin_time=excluded.origin_time, epicenter_location=excluded.epicenter_location,
                epicenter_lat=excluded.epicenter_lat, epicenter_lon=excluded.epicenter_lon,
                focal_depth=excluded.focal_depth, magnitude=excluded.magnitude, web_url=excluded.web_url""",
            records,
        )
    return len(records)
