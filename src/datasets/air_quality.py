"""Normalize and store MOENV AQI point observations."""
from typing import Any, Dict, List, Optional

from src.database import get_db_connection


def _as_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.lower() not in {"na", "n/a", "null", "none", "-"} else None


def _as_float(value: Any) -> Optional[float]:
    text = _as_text(value)
    if text is None:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _records(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        raise ValueError("環境部 API 回應不是 JSON 物件或資料列清單。")
    for key in ("records", "Records", "data", "Data"):
        node = payload.get(key)
        if isinstance(node, list):
            return [row for row in node if isinstance(row, dict)]
        if isinstance(node, dict):
            for nested_key in ("records", "Records", "data", "Data"):
                nested = node.get(nested_key)
                if isinstance(nested, list):
                    return [row for row in nested if isinstance(row, dict)]
    raise ValueError("環境部 API 回應中找不到 records 資料列。")


def parse_air_quality(payload: Any) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    for row in _records(payload):
        fields = {str(key).strip().lower().replace("_", ""): value for key, value in row.items()}

        def get(*names: str) -> Any:
            for name in names:
                key = name.lower().replace("_", "")
                if key in fields:
                    return fields[key]
            return None

        site_id = _as_text(get("SiteId", "SiteID", "siteid"))
        site_name = _as_text(get("SiteName", "Site"))
        latitude = _as_float(get("Latitude", "lat"))
        longitude = _as_float(get("Longitude", "lon", "lng"))
        publish_time = _as_text(get("publishtime", "DataCreationDate", "PublishTime", "ImportDate"))
        if not site_id or not site_name or latitude is None or longitude is None:
            continue
        if not publish_time:
            publish_time = "時間未提供"
        parsed.append({
            "site_id": site_id,
            "site_name": site_name,
            "county_name": _as_text(get("County", "county")),
            "publish_time": publish_time,
            "aqi": _as_float(get("AQI")),
            "status": _as_text(get("Status")),
            "pollutant": _as_text(get("Pollutant")),
            "pm25": _as_float(get("PM2.5", "PM25")),
            "pm25_avg": _as_float(get("PM2.5_AVG", "PM25_AVG")),
            "pm10": _as_float(get("PM10")),
            "ozone": _as_float(get("O3")),
            "longitude": longitude,
            "latitude": latitude,
        })
    return parsed


def save_air_quality(records: List[Dict[str, Any]]) -> int:
    if not records:
        return 0
    with get_db_connection() as conn:
        conn.executemany(
            """INSERT INTO air_quality_observations
               (site_id,site_name,county_name,publish_time,aqi,status,pollutant,pm25,pm25_avg,pm10,ozone,longitude,latitude)
               VALUES (:site_id,:site_name,:county_name,:publish_time,:aqi,:status,:pollutant,:pm25,:pm25_avg,:pm10,:ozone,:longitude,:latitude)
               ON CONFLICT(site_id,publish_time) DO UPDATE SET
                 site_name=excluded.site_name,county_name=excluded.county_name,aqi=excluded.aqi,
                 status=excluded.status,pollutant=excluded.pollutant,pm25=excluded.pm25,
                 pm25_avg=excluded.pm25_avg,pm10=excluded.pm10,ozone=excluded.ozone,
                 longitude=excluded.longitude,latitude=excluded.latitude,updated_at=CURRENT_TIMESTAMP""",
            records,
        )
    return len(records)
