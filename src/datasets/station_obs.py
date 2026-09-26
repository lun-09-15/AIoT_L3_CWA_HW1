"""
Weather Station Observations dataset parser (O-A0001-001).
Parses real-time weather stations data and stores into station_observations SQLite table.
"""

from typing import Any, Dict, List, Optional
from src.database import get_db_connection

DATASET_ID = "O-A0001-001"
MISSING_VALUES = {"-99", "-999", "-99.0", "-999.0", "X", "", None}

def _to_float(val: Any) -> Optional[float]:
    """Safely converts value to float, treating missing codes as None."""
    if val in MISSING_VALUES:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None

def parse_station_observations(raw_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Parses O-A0001-001 JSON into structured records.
    """
    records_node = raw_json.get("records", {})
    stations = records_node.get("Station", [])
    if not isinstance(stations, list):
        stations = [stations] if stations else []

    parsed_list = []

    for stn in stations:
        station_id = stn.get("StationId", "")
        station_name = stn.get("StationName", "")
        obs_time = stn.get("ObsTime", {}).get("DateTime", "")

        geo = stn.get("GeoInfo", {})
        county = geo.get("CountyName")
        town = geo.get("TownName")
        altitude = _to_float(geo.get("StationAltitude"))

        # Extract WGS84 coordinates
        lat, lon = None, None
        for coord in geo.get("Coordinates", []):
            if coord.get("CoordinateName") == "WGS84":
                lat = _to_float(coord.get("StationLatitude"))
                lon = _to_float(coord.get("StationLongitude"))
                break

        # Weather elements
        we = stn.get("WeatherElement", {})
        temp = _to_float(we.get("AirTemperature"))
        humidity = _to_float(we.get("RelativeHumidity"))
        wind_speed = _to_float(we.get("WindSpeed"))
        wind_dir = _to_float(we.get("WindDirection"))
        pressure = _to_float(we.get("AirPressure"))
        precip = _to_float(we.get("Now", {}).get("Precipitation"))

        parsed_list.append({
            "station_id": station_id,
            "station_name": station_name,
            "county_name": county,
            "town_name": town,
            "latitude": lat,
            "longitude": lon,
            "altitude": altitude,
            "obs_time": obs_time,
            "temperature": temp,
            "relative_humidity": humidity,
            "wind_speed": wind_speed,
            "wind_direction": wind_dir,
            "precipitation": precip,
            "air_pressure": pressure
        })

    return parsed_list

def save_station_observations(records: List[Dict[str, Any]]) -> int:
    """Inserts or updates station observations into SQLite."""
    if not records:
        return 0

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.executemany("""
            INSERT INTO station_observations (
                station_id, station_name, county_name, town_name, latitude, longitude,
                altitude, obs_time, temperature, relative_humidity, wind_speed,
                wind_direction, precipitation, air_pressure
            ) VALUES (
                :station_id, :station_name, :county_name, :town_name, :latitude, :longitude,
                :altitude, :obs_time, :temperature, :relative_humidity, :wind_speed,
                :wind_direction, :precipitation, :air_pressure
            )
            ON CONFLICT(station_id, obs_time) DO UPDATE SET
                station_name = excluded.station_name,
                county_name = excluded.county_name,
                town_name = excluded.town_name,
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                altitude = excluded.altitude,
                temperature = excluded.temperature,
                relative_humidity = excluded.relative_humidity,
                wind_speed = excluded.wind_speed,
                wind_direction = excluded.wind_direction,
                precipitation = excluded.precipitation,
                air_pressure = excluded.air_pressure,
                updated_at = CURRENT_TIMESTAMP
        """, records)
        return len(records)
