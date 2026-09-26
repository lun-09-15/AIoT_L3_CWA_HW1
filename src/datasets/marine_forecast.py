"""
Marine Forecast dataset parser (F-A0012-001).
Parses sea marine forecasts and stores into marine_forecasts SQLite table.
"""

from typing import Any, Dict, List, Optional
from src.database import get_db_connection

DATASET_ID = "F-A0012-001"

def parse_marine_forecast(raw_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Parses F-A0012-001 JSON into structured records.
    """
    root = raw_json.get("cwaopendata", {})
    dataset = root.get("Dataset", {})
    locations_node = dataset.get("Locations", {})
    locations = locations_node.get("Location", [])

    if not isinstance(locations, list):
        locations = [locations] if locations else []

    records = []

    for loc in locations:
        loc_name = loc.get("LocationName", "")
        # Organize elements by (StartTime, EndTime)
        time_slot_map = {}

        for elem in loc.get("WeatherElement", []):
            elem_name = elem.get("ElementName", "")
            for t in elem.get("Time", []):
                st = t.get("StartTime", "")
                et = t.get("EndTime", "")
                slot_key = (st, et)
                if slot_key not in time_slot_map:
                    time_slot_map[slot_key] = {
                        "location_name": loc_name,
                        "start_time": st,
                        "end_time": et,
                        "weather": None,
                        "weather_code": None,
                        "wind_direction": None,
                        "wind_speed": None,
                        "wave_height": None,
                        "wave_type": None,
                    }

                val_dict = t.get("ElementValue", {})
                if elem_name == "天氣現象":
                    time_slot_map[slot_key]["weather"] = val_dict.get("Weather")
                    time_slot_map[slot_key]["weather_code"] = val_dict.get("WeatherCode")
                elif elem_name in ("風向描述", "風向說明"):
                    time_slot_map[slot_key]["wind_direction"] = (
                        val_dict.get("WindDirectionDescription") or val_dict.get("WindDirection")
                    )
                elif elem_name in ("蒲福風級描述", "風速說明"):
                    time_slot_map[slot_key]["wind_speed"] = (
                        val_dict.get("BeaufortScaleDescription") or val_dict.get("WindSpeed")
                    )
                elif elem_name in ("浪高描述", "浪高說明"):
                    time_slot_map[slot_key]["wave_height"] = (
                        val_dict.get("WaveHeightDescription") or val_dict.get("WaveHeight")
                    )
                elif elem_name in ("浪型描述", "浪況說明"):
                    time_slot_map[slot_key]["wave_type"] = (
                        val_dict.get("WaveTypeDescription") or val_dict.get("WaveType")
                    )

        records.extend(time_slot_map.values())

    return records

def save_marine_forecast(records: List[Dict[str, Any]]) -> int:
    """Inserts or updates parsed marine forecast records in SQLite."""
    if not records:
        return 0

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.executemany("""
            INSERT INTO marine_forecasts (
                location_name, start_time, end_time, weather, weather_code,
                wind_direction, wind_speed, wave_height, wave_type
            ) VALUES (
                :location_name, :start_time, :end_time, :weather, :weather_code,
                :wind_direction, :wind_speed, :wave_height, :wave_type
            )
            ON CONFLICT(location_name, start_time, end_time) DO UPDATE SET
                weather = excluded.weather,
                weather_code = excluded.weather_code,
                wind_direction = excluded.wind_direction,
                wind_speed = excluded.wind_speed,
                wave_height = excluded.wave_height,
                wave_type = excluded.wave_type,
                updated_at = CURRENT_TIMESTAMP
        """, records)
        return len(records)
