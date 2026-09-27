"""Fetch, snapshot and store the current MOENV AQI station data."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.config import BASE_DIR, MOENV_API_KEY, RAW_SNAPSHOTS_DIR
from src.database import record_ingestion_run, record_raw_snapshot
from src.datasets.air_quality import parse_air_quality, save_air_quality
from src.moenv_client import MOENV_AQI_DATASET_ID, MOENVClient


def sync_air_quality() -> dict:
    if not MOENV_API_KEY:
        return {"status": "SKIPPED", "records": 0, "message": "尚未設定 MOENV_API_KEY。"}
    response_ms = None
    data_timestamp = None
    try:
        payload, response_ms = MOENVClient().fetch_aqi()
        records = parse_air_quality(payload)
        if not records:
            raise ValueError("環境部回應沒有含測站座標的有效 AQI 資料列。")

        timestamps = []
        for row in records:
            if row["publish_time"] == "時間未提供":
                continue
            value = row["publish_time"]
            try:
                parsed_time = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed_time.tzinfo is None:
                    parsed_time = parsed_time.replace(tzinfo=ZoneInfo("Asia/Taipei"))
                timestamps.append(parsed_time.isoformat(timespec="seconds"))
            except ValueError:
                timestamps.append(value)
        data_timestamp = max(timestamps) if timestamps else None
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S_%f")
        path = RAW_SNAPSHOTS_DIR / f"{MOENV_AQI_DATASET_ID}_{stamp}_{digest[:8]}.json"
        path.write_bytes(content)
        relative_path = path.relative_to(BASE_DIR).as_posix()
        record_raw_snapshot(MOENV_AQI_DATASET_ID, relative_path, digest, len(content), data_timestamp)

        count = save_air_quality(records)
        status = "SUCCESS" if count else "EMPTY"
        record_ingestion_run(
            MOENV_AQI_DATASET_ID, status, records_count=count,
            response_time_ms=response_ms, data_timestamp=data_timestamp,
        )
        return {"status": status, "records": count, "message": f"儲存/更新 {count} 個空品測站。"}
    except Exception as exc:
        message = str(exc)[:240]
        record_ingestion_run(
            MOENV_AQI_DATASET_ID, "FAILED", response_time_ms=response_ms,
            data_timestamp=data_timestamp, error_message=message,
        )
        return {"status": "FAILED", "records": 0, "message": message}


def main() -> int:
    if not MOENV_API_KEY:
        print("錯誤：請在專案根目錄 .env 設定 MOENV_API_KEY。", file=sys.stderr)
        return 2
    result = sync_air_quality()
    print(f"環境部空氣品質更新：{result['status']} · {result['message']}")
    return 1 if result["status"] == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
