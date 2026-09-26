"""
Database management module for CWA Weather Dashboard.
Provides SQLite connection management, schema initialization, and transactional query execution.
"""

import sqlite3
from typing import Any, Dict, List, Optional
from contextlib import contextmanager

from src.config import DB_PATH

@contextmanager
def get_db_connection():
    """Context manager for SQLite database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db() -> None:
    """Initializes database tables according to workflow.md multi-dataset specifications."""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 1. Ingestion Runs (記錄每次擷取日誌與狀態)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ingestion_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id TEXT NOT NULL,
                run_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL,           -- 'SUCCESS', 'EMPTY', 'FAILED'
                records_count INTEGER DEFAULT 0,
                response_time_ms REAL,
                data_timestamp TEXT,
                error_message TEXT
            );
        """)

        # 2. Raw Snapshots (原始快照雜湊與儲存索引)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS raw_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id TEXT NOT NULL,
                captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                data_timestamp TEXT,
                file_path TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                file_size_bytes INTEGER
            );
        """)

        # 3. Marine Forecasts (海面天氣預報 F-A0012-001)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS marine_forecasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                location_name TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                weather TEXT,
                weather_code TEXT,
                wind_direction TEXT,
                wind_speed TEXT,
                wave_height TEXT,
                wave_type TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(location_name, start_time, end_time)
            );
        """)

        # 4. Station Observations (測站氣象觀測 O-A0001-001)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS station_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                station_id TEXT NOT NULL,
                station_name TEXT NOT NULL,
                county_name TEXT,
                town_name TEXT,
                latitude REAL,
                longitude REAL,
                altitude REAL,
                obs_time TEXT NOT NULL,
                temperature REAL,
                relative_humidity REAL,
                wind_speed REAL,
                wind_direction REAL,
                precipitation REAL,
                air_pressure REAL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(station_id, obs_time)
            );
        """)

        # 5. Tsunami Events (海嘯資訊 E-A0014-001)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tsunami_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tsunami_no INTEGER,
                report_no TEXT,
                issue_time TEXT NOT NULL,
                valid_end_time TEXT,
                report_color TEXT,
                report_type TEXT,
                report_content TEXT,
                origin_time TEXT,
                epicenter_location TEXT,
                epicenter_lat REAL,
                epicenter_lon REAL,
                focal_depth REAL,
                magnitude REAL,
                web_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tsunami_no, report_no, issue_time)
            );
        """)

        # 6. Temperature Maps (溫度分布圖產品 O-A0038-001)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS temperature_maps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id TEXT NOT NULL,
                obs_time TEXT NOT NULL,
                image_url TEXT NOT NULL,
                local_image_path TEXT,
                lat_range TEXT,
                lon_range TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(dataset_id, obs_time)
            );
        """)

        # 7. Typhoon Tracks (熱帶氣旋路徑分析與預報 W-C0034-005)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS typhoon_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                typhoon_name TEXT NOT NULL,
                cwa_name TEXT,
                year INTEGER,
                record_type TEXT NOT NULL,      -- 'ANALYSIS' or 'FORECAST'
                fix_time TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                pressure REAL,
                max_wind_speed REAL,
                gust REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(typhoon_name, record_type, fix_time)
            );
        """)

        # 8. Typhoon Probabilities (颱風侵襲機率圖層 W-C0034-003)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS typhoon_probabilities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id TEXT NOT NULL,
                captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                kmz_path TEXT NOT NULL,
                polygon_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

def record_ingestion_run(
    dataset_id: str,
    status: str,
    records_count: int = 0,
    response_time_ms: float = 0.0,
    data_timestamp: Optional[str] = None,
    error_message: Optional[str] = None
) -> int:
    """Records an ingestion execution log in ingestion_runs."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO ingestion_runs (
                dataset_id, status, records_count, response_time_ms, data_timestamp, error_message
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (dataset_id, status, records_count, response_time_ms, data_timestamp, error_message))
        return cursor.lastrowid

def record_raw_snapshot(
    dataset_id: str,
    file_path: str,
    content_sha256: str,
    file_size_bytes: int,
    data_timestamp: Optional[str] = None
) -> int:
    """Records raw payload snapshot in raw_snapshots."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO raw_snapshots (
                dataset_id, file_path, content_sha256, file_size_bytes, data_timestamp
            ) VALUES (?, ?, ?, ?, ?)
        """, (dataset_id, file_path, content_sha256, file_size_bytes, data_timestamp))
        return cursor.lastrowid

def get_latest_ingestion_summary() -> List[Dict[str, Any]]:
    """Returns the most recent ingestion status for each dataset."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r1.*
            FROM ingestion_runs r1
            INNER JOIN (
                SELECT dataset_id, MAX(id) as max_id
                FROM ingestion_runs
                GROUP BY dataset_id
            ) r2 ON r1.id = r2.max_id
            ORDER BY r1.run_time DESC
        """)
        return [dict(row) for row in cursor.fetchall()]
