"""
Temperature Distribution Map product parser (O-A0038-001).
Parses temperature distribution image metadata and stores into temperature_maps SQLite table.
"""

from typing import Any, Dict, Optional
from src.database import get_db_connection

DATASET_ID = "O-A0038-001"

def parse_temperature_map(raw_json: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Parses O-A0038-001 JSON into structured map metadata.
    """
    root = raw_json.get("cwaopendata", {})
    dataset = root.get("dataset", {})
    if not dataset:
        dataset = root.get("Dataset", {})

    obs_time = dataset.get("ObsTime", {}).get("DateTime", "")
    geo = dataset.get("GeoInfo", {})
    lat_range = geo.get("LatitudeRange")
    lon_range = geo.get("LongitudeRange")

    resource = dataset.get("Resource", {})
    image_url = resource.get("ProductURL", "")

    if not obs_time or not image_url:
        return None

    return {
        "dataset_id": DATASET_ID,
        "obs_time": obs_time,
        "image_url": image_url,
        "local_image_path": None,
        "lat_range": lat_range,
        "lon_range": lon_range
    }

def save_temperature_map(record: Optional[Dict[str, Any]]) -> int:
    """Inserts or updates temperature map metadata in SQLite."""
    if not record:
        return 0

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO temperature_maps (
                dataset_id, obs_time, image_url, local_image_path, lat_range, lon_range
            ) VALUES (
                :dataset_id, :obs_time, :image_url, :local_image_path, :lat_range, :lon_range
            )
            ON CONFLICT(dataset_id, obs_time) DO UPDATE SET
                image_url = excluded.image_url,
                lat_range = excluded.lat_range,
                lon_range = excluded.lon_range
        """, record)
        return 1
