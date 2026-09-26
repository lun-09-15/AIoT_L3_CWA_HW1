"""
Storage coordinator module.
Dispatches raw dataset payloads to their dedicated parsers and database loaders.
"""

from typing import Any, Dict, Optional, Tuple, Union
from src.datasets.marine_forecast import parse_marine_forecast, save_marine_forecast
from src.datasets.station_obs import parse_station_observations, save_station_observations
from src.datasets.tsunami import parse_tsunami, save_tsunami_events
from src.datasets.temperature_map import parse_temperature_map, save_temperature_map
from src.datasets.typhoon import (
    parse_typhoon_tracks,
    save_typhoon_tracks,
    parse_and_save_typhoon_probability_kmz
)

def process_and_store_dataset(
    dataset_id: str,
    payload: Union[Dict[str, Any], bytes],
    file_path: Optional[str] = None
) -> Tuple[bool, int, str]:
    """
    Routes payload to corresponding parser and saves to SQLite.
    Returns: (success: bool, records_count: int, message: str)
    """
    try:
        if dataset_id == "F-A0012-001":
            if not isinstance(payload, dict):
                return False, 0, "Payload must be JSON dict"
            records = parse_marine_forecast(payload)
            count = save_marine_forecast(records)
            return True, count, f"Saved {count} marine forecast records"

        elif dataset_id == "O-A0001-001":
            if not isinstance(payload, dict):
                return False, 0, "Payload must be JSON dict"
            records = parse_station_observations(payload)
            count = save_station_observations(records)
            return True, count, f"Saved {count} station observation records"

        elif dataset_id == "E-A0014-001":
            if not isinstance(payload, dict):
                return False, 0, "Payload must be JSON dict"
            records = parse_tsunami(payload)
            count = save_tsunami_events(records)
            return True, count, f"Saved {count} tsunami event records"

        elif dataset_id == "O-A0038-001":
            if not isinstance(payload, dict):
                return False, 0, "Payload must be JSON dict"
            record = parse_temperature_map(payload)
            count = save_temperature_map(record)
            return True, count, f"Saved {count} temperature map product metadata"

        elif dataset_id == "W-C0034-005":
            if not isinstance(payload, dict):
                return False, 0, "Payload must be JSON dict"
            records = parse_typhoon_tracks(payload)
            count = save_typhoon_tracks(records)
            return True, count, f"Saved {count} typhoon track points"

        elif dataset_id == "W-C0034-003":
            if not isinstance(payload, bytes):
                return False, 0, "Payload must be KMZ bytes"
            result = parse_and_save_typhoon_probability_kmz(payload, file_path or "data/snapshots/W-C0034-003.kmz")
            return True, 1, f"Saved KMZ probability layer with {result.get('polygon_count', 0)} polygons"

        else:
            return False, 0, f"Unsupported dataset_id: {dataset_id}"

    except Exception as e:
        return False, 0, f"Processing error for {dataset_id}: {str(e)}"
