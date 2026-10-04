"""Fetch configured CWA datasets and persist normalized records."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import sys
from datetime import datetime
from typing import Dict, Optional

from src.cwa_client import CWAClient
from src.config import CWA_API_KEY, DB_PATH
from src.database import init_db, record_ingestion_run
from src.datasets.specs import DATASET_SPECS
from src.storage import process_and_store_dataset


def ingest_all(client: CWAClient, dataset_ids: Optional[list[str]] = None) -> Dict[str, dict]:
    """Fetch datasets with bounded concurrency, then persist in one thread."""
    targets = list(DATASET_SPECS) if dataset_ids is None else list(dataset_ids)
    if not targets:
        return {}

    stop_after_connection_failure = Event()

    def fetch_one(dataset_id: str) -> dict:
        spec = DATASET_SPECS.get(dataset_id)
        if spec is None:
            return {"status": "SKIPPED", "records": 0, "message": "Unknown dataset"}
        if stop_after_connection_failure.is_set():
            return {"status": "FAILED", "records": 0, "message": "CWA 連線失敗，略過同一輪其餘待執行請求。"}

        worker_client = CWAClient(api_key=client.api_key)
        try:
            if spec.api_type == "REST":
                success, payload, message = worker_client.fetch_rest_dataset(dataset_id)
            elif spec.api_type == "FILE":
                success, payload, message = worker_client.fetch_file_dataset(dataset_id, spec.file_format)
            else:
                success, payload, message = False, None, f"Unsupported API type: {spec.api_type}"
            if worker_client.connection_failure:
                stop_after_connection_failure.set()
            return {
                "status": "FETCHED" if success and payload is not None else "FAILED",
                "records": 0,
                "payload": payload,
                "snapshot_path": worker_client.last_snapshot_paths.get(dataset_id),
                "response_time_ms": worker_client.last_response_times_ms.get(dataset_id),
                "data_timestamp": worker_client.last_data_timestamps.get(dataset_id),
                "message": message,
            }
        except Exception as exc:
            message = worker_client._safe_error(exc)
            if worker_client.connection_failure:
                stop_after_connection_failure.set()
            return {"status": "FAILED", "records": 0, "message": message}
        finally:
            worker_client.session.close()

    # Each worker owns its HTTP session. Four concurrent requests reduce wait
    # time without flooding CWA; SQLite dataset writes below stay sequential.
    workers = min(4, len(targets))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cwa-dataset") as pool:
        futures = {dataset_id: pool.submit(fetch_one, dataset_id) for dataset_id in targets}
        fetched = {dataset_id: future.result() for dataset_id, future in futures.items()}

    summary: Dict[str, dict] = {}
    for dataset_id in targets:
        spec = DATASET_SPECS.get(dataset_id)
        result = fetched[dataset_id]
        if spec is None:
            summary[dataset_id] = result
            continue

        print(f"\n[{dataset_id}] {spec.official_name}")
        print(f"  介面：{spec.api_type} / {spec.file_format}；更新頻率：{spec.update_frequency}")
        if result["status"] != "FETCHED":
            record_ingestion_run(
                dataset_id, "FAILED", response_time_ms=result.get("response_time_ms"),
                data_timestamp=result.get("data_timestamp"), error_message=result["message"],
            )
            summary[dataset_id] = {"status": "FAILED", "records": 0, "message": result["message"]}
            print(f"  失敗：{result['message']}")
            continue

        ok, count, store_message = process_and_store_dataset(
            dataset_id, result["payload"], result.get("snapshot_path")
        )
        if not ok:
            record_ingestion_run(
                dataset_id, "FAILED", response_time_ms=result.get("response_time_ms"),
                data_timestamp=result.get("data_timestamp"), error_message=store_message,
            )
            summary[dataset_id] = {"status": "FAILED", "records": 0, "message": store_message}
            print(f"  解析/儲存失敗：{store_message}")
            continue

        status = "SUCCESS" if count else "EMPTY"
        record_ingestion_run(
            dataset_id, status, records_count=count,
            response_time_ms=result.get("response_time_ms"),
            data_timestamp=result.get("data_timestamp"),
        )
        summary[dataset_id] = {"status": status, "records": count, "message": store_message}
        print(f"  {status}：{store_message}")
    return summary
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="匯入中央氣象署開放資料")
    parser.add_argument("--only", nargs="+", choices=sorted(DATASET_SPECS), help="只匯入指定資料集")
    args = parser.parse_args(argv)
    print(f"CWA 資料匯入｜{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"資料庫：{DB_PATH}")
    if not CWA_API_KEY:
        print("錯誤：尚未設定 CWA_API_KEY。請在專案根目錄 .env 填入授權碼。", file=sys.stderr)
        return 2
    init_db()
    try:
        summary = ingest_all(CWAClient(), args.only)
    except Exception as exc:
        print(f"匯入中止：{exc}", file=sys.stderr)
        return 1
    print("\n匯入摘要")
    for dataset_id, result in summary.items():
        print(f"  {dataset_id}: {result['status']} ({result.get('records', 0)}) — {result['message']}")
    return 1 if any(row["status"] == "FAILED" for row in summary.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
