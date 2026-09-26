"""
Marine forecast parser for CWA dataset F-A0012-001.

Records are one sea area and one validity interval, with weather elements joined by
(StartTime, EndTime). Text descriptions are kept as published by CWA.
"""
from typing import Any, Dict, List, Optional
from src.database import get_db_connection

DATASET_ID = "F-A0012-001"


def _items(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _first(values: Dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = values.get(key)
        if value not in (None, "", "-"):
            return str(value)
    return None


def parse_marine_forecast(raw_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    root = raw_json.get("cwaopendata", {})
    dataset = root.get("Dataset") or root.get("dataset") or {}
    locations_node = dataset.get("Locations") or dataset.get("locations") or {}
    locations = _items(locations_node.get("Location") or locations_node.get("location"))
    records: List[Dict[str, Any]] = []
    for location in locations:
        location_name = location.get("LocationName") or location.get("locationName")
        if not location_name:
            continue
        slots: Dict[tuple, Dict[str, Any]] = {}
        elements = location.get("WeatherElement") or location.get("weatherElement") or []
        for element in _items(elements):
            element_name = str(element.get("ElementName") or element.get("elementName") or "")
            for period in _items(element.get("Time") or element.get("time")):
                start = period.get("StartTime") or period.get("startTime")
                end = period.get("EndTime") or period.get("endTime")
                if not start or not end:
                    continue
                key = (str(start), str(end))
                row = slots.setdefault(key, {
                    "location_name": str(location_name), "start_time": str(start), "end_time": str(end),
                    "weather": None, "weather_code": None, "wind_direction": None, "wind_speed": None,
                    "wave_height": None, "wave_type": None,
                })
                value = period.get("ElementValue") or period.get("elementValue") or {}
                if not isinstance(value, dict):
                    continue
                # Prefer the value keys; ElementName labels differ slightly by product revision.
                weather = _first(value, "Weather", "Wx")
                if weather:
                    row["weather"] = weather
                    row["weather_code"] = _first(value, "WeatherCode", "WxCode")
                direction = _first(value, "WindDirectionDescription", "WindDirection", "WindDir")
                if direction:
                    row["wind_direction"] = direction
                wind = _first(value, "BeaufortScaleDescription", "WindSpeedDescription", "WindSpeed")
                if wind:
                    row["wind_speed"] = wind
                wave_height = _first(value, "WaveHeightDescription", "WaveHeight")
                if wave_height:
                    row["wave_height"] = wave_height
                wave_type = _first(value, "WaveTypeDescription", "WaveType")
                if wave_type:
                    row["wave_type"] = wave_type
                # Older payload variants may provide generic parameter fields.
                if element_name and not any((weather, direction, wind, wave_height, wave_type)):
                    generic = _first(value, "parameterName", "ParameterName", "value")
                    if generic:
                        if "天氣" in element_name:
                            row["weather"] = generic
                        elif "風向" in element_name:
                            row["wind_direction"] = generic
                        elif "風速" in element_name:
                            row["wind_speed"] = generic
                        elif "浪高" in element_name:
                            row["wave_height"] = generic
                        elif "浪況" in element_name or "浪型" in element_name:
                            row["wave_type"] = generic
        records.extend(slots.values())
    return records


def save_marine_forecast(records: List[Dict[str, Any]]) -> int:
    if not records:
        return 0
    with get_db_connection() as conn:
        conn.executemany(
            """INSERT INTO marine_forecasts
               (location_name,start_time,end_time,weather,weather_code,wind_direction,wind_speed,wave_height,wave_type)
               VALUES (:location_name,:start_time,:end_time,:weather,:weather_code,:wind_direction,:wind_speed,:wave_height,:wave_type)
               ON CONFLICT(location_name,start_time,end_time) DO UPDATE SET
                 weather=excluded.weather, weather_code=excluded.weather_code,
                 wind_direction=excluded.wind_direction, wind_speed=excluded.wind_speed,
                 wave_height=excluded.wave_height, wave_type=excluded.wave_type,
                 updated_at=CURRENT_TIMESTAMP""",
            records,
        )
    return len(records)
