"""Route normalized CWA payloads to their dataset parser and SQLite table."""
from typing import Any, Dict, Optional, Tuple, Union

from src.datasets.marine_forecast import parse_marine_forecast, save_marine_forecast
from src.datasets.station_obs import parse_station_observations, save_station_observations
from src.datasets.temperature_map import parse_temperature_map, save_temperature_map
from src.datasets.tsunami import parse_tsunami, save_tsunami_events
from src.datasets.typhoon import (
    parse_and_save_typhoon_probability_kmz, parse_typhoon_tracks, save_typhoon_tracks,
)


def process_and_store_dataset(dataset_id: str, payload: Union[Dict[str, Any], bytes], file_path: Optional[str] = None) -> Tuple[bool, int, str]:
    try:
        if dataset_id == "F-A0012-001" and isinstance(payload, dict):
            rows = parse_marine_forecast(payload); count = save_marine_forecast(rows)
            return True, count, f"儲存 {count} 筆海面預報時段"
        if dataset_id == "O-A0001-001" and isinstance(payload, dict):
            rows = parse_station_observations(payload); count = save_station_observations(rows)
            return True, count, f"儲存/更新 {count} 筆測站觀測"
        if dataset_id == "E-A0014-001" and isinstance(payload, dict):
            rows = parse_tsunami(payload); count = save_tsunami_events(rows)
            return True, count, f"儲存/更新 {count} 筆海嘯報告"
        if dataset_id == "O-A0038-001" and isinstance(payload, dict):
            row = parse_temperature_map(payload); count = save_temperature_map(row)
            return True, count, f"儲存 {count} 筆溫度分布圖 metadata"
        if dataset_id == "W-C0034-005" and isinstance(payload, dict):
            rows = parse_typhoon_tracks(payload); count = save_typhoon_tracks(rows)
            return True, count, f"儲存/更新 {count} 個熱帶氣旋定位點"
        if dataset_id == "W-C0034-003" and isinstance(payload, bytes):
            if not file_path:
                return False, 0, "找不到 KMZ 快照路徑。"
            result = parse_and_save_typhoon_probability_kmz(payload, file_path)
            return True, result["polygon_count"], f"儲存颱風機率圖層（{result['polygon_count']} 個多邊形）"
        return False, 0, f"{dataset_id} 的 payload 格式不符或資料集不支援。"
    except Exception as exc:
        return False, 0, f"處理 {dataset_id} 失敗：{str(exc)[:240]}"
