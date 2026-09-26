"""
Tsunami Information dataset parser (E-A0014-001).
Parses tsunami bulletins and earthquake details into tsunami_events SQLite table.
"""

from typing import Any, Dict, List, Optional
from src.database import get_db_connection

DATASET_ID = "E-A0014-001"

def parse_tsunami(raw_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Parses E-A0014-001 JSON into structured tsunami event records.
    """
    records_node = raw_json.get("records", {})
    tsunami_list = records_node.get("Tsunami", [])
    if not isinstance(tsunami_list, list):
        tsunami_list = [tsunami_list] if tsunami_list else []

    parsed_list = []

    for item in tsunami_list:
        tsunami_no = item.get("TsunamiNo")
        report_no = str(item.get("ReportNo", ""))
        issue_time = item.get("IssueTime", "")
        valid_end_time = item.get("ValidTime", {}).get("EndTime") if isinstance(item.get("ValidTime"), dict) else None
        report_color = item.get("ReportColor")
        report_type = item.get("ReportType")
        report_content = item.get("ReportContent")
        web_url = item.get("Web")

        eq = item.get("EarthquakeInfo", {})
        origin_time = eq.get("OriginTime")
        focal_depth = eq.get("FocalDepth")
        try:
            focal_depth = float(focal_depth) if focal_depth is not None else None
        except (ValueError, TypeError):
            focal_depth = None

        magnitude = eq.get("EarthquakeMagnitude", {}).get("MagnitudeValue")
        try:
            magnitude = float(magnitude) if magnitude is not None else None
        except (ValueError, TypeError):
            magnitude = None

        epicenter = eq.get("Epicenter", {})
        epicenter_loc = epicenter.get("Location")
        lat = epicenter.get("EpicenterLatitude")
        lon = epicenter.get("EpicenterLongitude")
        try:
            lat = float(lat) if lat is not None else None
            lon = float(lon) if lon is not None else None
        except (ValueError, TypeError):
            lat, lon = None, None

        parsed_list.append({
            "tsunami_no": tsunami_no,
            "report_no": report_no,
            "issue_time": issue_time,
            "valid_end_time": valid_end_time,
            "report_color": report_color,
            "report_type": report_type,
            "report_content": report_content,
            "origin_time": origin_time,
            "epicenter_location": epicenter_loc,
            "epicenter_lat": lat,
            "epicenter_lon": lon,
            "focal_depth": focal_depth,
            "magnitude": magnitude,
            "web_url": web_url,
        })

    return parsed_list

def save_tsunami_events(records: List[Dict[str, Any]]) -> int:
    """Inserts or updates tsunami event records into SQLite."""
    if not records:
        return 0

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.executemany("""
            INSERT INTO tsunami_events (
                tsunami_no, report_no, issue_time, valid_end_time, report_color,
                report_type, report_content, origin_time, epicenter_location,
                epicenter_lat, epicenter_lon, focal_depth, magnitude, web_url
            ) VALUES (
                :tsunami_no, :report_no, :issue_time, :valid_end_time, :report_color,
                :report_type, :report_content, :origin_time, :epicenter_location,
                :epicenter_lat, :epicenter_lon, :focal_depth, :magnitude, :web_url
            )
            ON CONFLICT(tsunami_no, report_no, issue_time) DO UPDATE SET
                valid_end_time = excluded.valid_end_time,
                report_color = excluded.report_color,
                report_type = excluded.report_type,
                report_content = excluded.report_content,
                epicenter_location = excluded.epicenter_location,
                epicenter_lat = excluded.epicenter_lat,
                epicenter_lon = excluded.epicenter_lon,
                focal_depth = excluded.focal_depth,
                magnitude = excluded.magnitude,
                web_url = excluded.web_url
        """, records)
        return len(records)
