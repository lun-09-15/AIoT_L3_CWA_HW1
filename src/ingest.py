"""
Main ingestion script for CWA Weather Dashboard.
Fetches all 6 datasets from CWA Open Data, parses them, and stores into SQLite.

Usage:
    python -m src.ingest          # Fetch all datasets
    python -m src.ingest --only O-A0001-001   # Fetch a single dataset
"""

import sys
import argparse
from datetime import datetime

from src.config import CWA_API_KEY, DB_PATH
from src.database import init_db, record_ingestion_run
from src.cwa_client import CWAClient
from src.storage import process_and_store_dataset
from src.datasets.specs import DATASET_SPECS


def ingest_all(client: CWAClient, dataset_ids: list[str] | None = None) -> dict:
    """
    Runs ingestion for all (or selected) datasets.
    Returns a summary dict keyed by dataset_id.
    """
    targets = dataset_ids or list(DATASET_SPECS.keys())
    summary = {}

    for ds_id in targets:
        spec = DATASET_SPECS.get(ds_id)
        if not spec:
            print(f"  ⚠️  Unknown dataset_id: {ds_id}, skipping.")
            summary[ds_id] = {"status": "SKIPPED", "message": "Unknown dataset"}
            continue

        print(f"\n{'='*60}")
        print(f"  📥 Fetching [{ds_id}] {spec.official_name}")
        print(f"     API type: {spec.api_type} | Format: {spec.file_format}")
        print(f"{'='*60}")

        # --- Fetch ---
        if spec.api_type == "REST":
            success, payload, msg = client.fetch_rest_dataset(ds_id)
        elif spec.api_type == "FILE":
            success, payload, msg = client.fetch_file_dataset(ds_id, file_format=spec.file_format)
        else:
            print(f"  ❌ Unsupported api_type: {spec.api_type}")
            summary[ds_id] = {"status": "FAILED", "message": f"Unsupported api_type: {spec.api_type}"}
            continue

        if not success or payload is None:
            print(f"  ❌ Fetch failed: {msg}")
            summary[ds_id] = {"status": "FAILED", "message": msg}
            continue

        print(f"  ✅ Fetch success: {msg}")

        # --- Parse & Store ---
        ok, count, store_msg = process_and_store_dataset(ds_id, payload)
        if ok:
            print(f"  💾 Stored: {store_msg}")
            summary[ds_id] = {"status": "SUCCESS", "records": count, "message": store_msg}
            # Update ingestion run with record count
            record_ingestion_run(ds_id, status="SUCCESS", records_count=count)
        else:
            print(f"  ❌ Store failed: {store_msg}")
            summary[ds_id] = {"status": "FAILED", "message": store_msg}
            record_ingestion_run(ds_id, status="FAILED", error_message=store_msg)

    return summary


def main():
    parser = argparse.ArgumentParser(description="CWA Weather Dashboard - Data Ingestion")
    parser.add_argument(
        "--only",
        nargs="*",
        help="Only fetch specific dataset IDs (e.g. O-A0001-001 E-A0014-001)"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  🌦️  CWA Weather Dashboard - Data Ingestion")
    print(f"  🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  🔑 API Key: {'***' + CWA_API_KEY[-6:] if CWA_API_KEY else '❌ MISSING'}")
    print(f"  🗄️  DB Path: {DB_PATH}")
    print("=" * 60)

    if not CWA_API_KEY:
        print("\n❌ CWA_API_KEY is not set. Please configure it in .env file.")
        sys.exit(1)

    # 1. Initialize database tables
    print("\n📋 Initializing database tables...")
    init_db()
    print("   ✅ Database tables ready.")

    # 2. Create client and run ingestion
    client = CWAClient()
    summary = ingest_all(client, dataset_ids=args.only)

    # 3. Print summary
    print("\n" + "=" * 60)
    print("  📊 Ingestion Summary")
    print("=" * 60)
    for ds_id, result in summary.items():
        icon = "✅" if result["status"] == "SUCCESS" else "❌" if result["status"] == "FAILED" else "⚠️"
        count_str = f" ({result.get('records', 0)} records)" if result["status"] == "SUCCESS" else ""
        print(f"  {icon} {ds_id}: {result['status']}{count_str}")
    print("=" * 60)

    failed = [ds for ds, r in summary.items() if r["status"] == "FAILED"]
    if failed:
        print(f"\n⚠️  {len(failed)} dataset(s) failed: {', '.join(failed)}")
    else:
        print("\n🎉 All datasets ingested successfully!")


if __name__ == "__main__":
    main()
