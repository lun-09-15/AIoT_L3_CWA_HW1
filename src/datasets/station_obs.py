"""Parser and upsert logic for hourly CWA automatic weather-station observations."""
from typing import Any, Dict, List, Optional
from src.database import get_db_connection

DATASET_ID = "O-A0001-001"
MISSING_VALUES = {"-99", "-999", "-99.0", "-999.0", "X", "", "None"}


def _to_float(value: Any) -> Optional[float]:
    if value is None or str(value).strip() in MISSING_VALUES:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _items(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict): return [value]
    if isinstance(value, list): return [item for item in value if isinstance(item, dict)]
    return []


def parse_station_observations(raw_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    records_node = raw_json.get("records", {}) if isinstance(raw_json, dict) else {}
    stations = _items(records_node.get("Station")) if isinstance(records_node, dict) else []
    parsed: List[Dict[str, Any]] = []
    for station in stations:
        station_id = station.get("StationId")
        station_name = station.get("StationName")
        obs_time = (station.get("ObsTime") or {}).get("DateTime")
        if not station_id or not station_name or not obs_time:
            continue
        geo = station.get("GeoInfo") or {}
        latitude = longitude = None
        for coordinate in _items(geo.get("Coordinates")):
            if str(coordinate.get("CoordinateName", "")).upper() == "WGS84":
                latitude = _to_float(coordinate.get("StationLatitude"))
                longitude = _to_float(coordinate.get("StationLongitude"))
                break
        weather = station.get("WeatherElement") or {}
        now = weather.get("Now") or {}
        parsed.append({
            "station_id": str(station_id), "station_name": str(station_name),
            "county_name": geo.get("CountyName"), "town_name": geo.get("TownName"),
            "latitude": latitude, "longitude": longitude, "altitude": _to_float(geo.get("StationAltitude")),
            "obs_time": str(obs_time), "temperature": _to_float(weather.get("AirTemperature")),
            "relative_humidity": _to_float(weather.get("RelativeHumidity")),
            "wind_speed": _to_float(weather.get("WindSpeed")), "wind_direction": _to_float(weather.get("WindDirection")),
            "precipitation": _to_float(now.get("Precipitation")), "air_pressure": _to_float(weather.get("AirPressure")),
        })
    return parsed


def save_station_observations(records: List[Dict[str, Any]]) -> int:
    if not records: return 0
    with get_db_connection() as conn:
        conn.executemany(
            """INSERT INTO station_observations
               (station_id,station_name,county_name,town_name,latitude,longitude,altitude,obs_time,temperature,
                relative_humidity,wind_speed,wind_direction,precipitation,air_pressure)
               VALUES (:station_id,:station_name,:county_name,:town_name,:latitude,:longitude,:altitude,:obs_time,
                :temperature,:relative_humidity,:wind_speed,:wind_direction,:precipitation,:air_pressure)
               ON CONFLICT(station_id,obs_time) DO UPDATE SET
                station_name=excluded.station_name,county_name=excluded.county_name,town_name=excluded.town_name,
                latitude=excluded.latitude,longitude=excluded.longitude,altitude=excluded.altitude,
                temperature=excluded.temperature,relative_humidity=excluded.relative_humidity,
                wind_speed=excluded.wind_speed,wind_direction=excluded.wind_direction,
                precipitation=excluded.precipitation,air_pressure=excluded.air_pressure,updated_at=CURRENT_TIMESTAMP""",
            records,
        )
    return len(records)
