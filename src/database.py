"""SQLite persistence and query helpers for the six CWA datasets."""
from contextlib import contextmanager
import sqlite3
from typing import Any, Dict, Iterator, List, Optional, Sequence

from src.config import DB_PATH


@contextmanager
def get_db_connection() -> Iterator[sqlite3.Connection]:
    """Open a transaction; commit on success and roll back on failure."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create the operational tables used by ingestion and the dashboard."""
    schema = """
    CREATE TABLE IF NOT EXISTS ingestion_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset_id TEXT NOT NULL,
        run_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        status TEXT NOT NULL,
        records_count INTEGER NOT NULL DEFAULT 0,
        response_time_ms REAL,
        data_timestamp TEXT,
        error_message TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_ingestion_runs_dataset ON ingestion_runs(dataset_id, id DESC);

    CREATE TABLE IF NOT EXISTS raw_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset_id TEXT NOT NULL,
        captured_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        data_timestamp TEXT,
        file_path TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        file_size_bytes INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_raw_snapshots_dataset ON raw_snapshots(dataset_id, id DESC);

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
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(location_name, start_time, end_time)
    );
    CREATE INDEX IF NOT EXISTS idx_marine_time ON marine_forecasts(start_time);

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
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(station_id, obs_time)
    );
    CREATE INDEX IF NOT EXISTS idx_station_time ON station_observations(obs_time);
    CREATE INDEX IF NOT EXISTS idx_station_county ON station_observations(county_name);

    CREATE TABLE IF NOT EXISTS tsunami_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tsunami_no INTEGER,
        report_no TEXT NOT NULL DEFAULT '',
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
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tsunami_no, report_no, issue_time)
    );
    CREATE INDEX IF NOT EXISTS idx_tsunami_issue ON tsunami_events(issue_time DESC);

    CREATE TABLE IF NOT EXISTS temperature_maps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset_id TEXT NOT NULL,
        obs_time TEXT NOT NULL,
        image_url TEXT NOT NULL,
        local_image_path TEXT,
        lat_range TEXT,
        lon_range TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(dataset_id, obs_time)
    );

    CREATE TABLE IF NOT EXISTS typhoon_tracks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        typhoon_name TEXT NOT NULL,
        cwa_name TEXT,
        year INTEGER,
        record_type TEXT NOT NULL,
        fix_time TEXT NOT NULL,
        latitude REAL NOT NULL,
        longitude REAL NOT NULL,
        pressure REAL,
        max_wind_speed REAL,
        gust REAL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(typhoon_name, record_type, fix_time)
    );
    CREATE INDEX IF NOT EXISTS idx_typhoon_fix_time ON typhoon_tracks(fix_time);

    CREATE TABLE IF NOT EXISTS typhoon_probabilities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset_id TEXT NOT NULL,
        captured_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        product_time TEXT,
        kmz_path TEXT NOT NULL,
        polygon_count INTEGER NOT NULL DEFAULT 0,
        content_sha256 TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_typhoon_probability_time ON typhoon_probabilities(id DESC);
    """
    with get_db_connection() as conn:
        conn.executescript(schema)
        # Migrate databases created by earlier versions without discarding data.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(typhoon_probabilities)")}
        if "product_time" not in columns:
            conn.execute("ALTER TABLE typhoon_probabilities ADD COLUMN product_time TEXT")
        if "content_sha256" not in columns:
            conn.execute("ALTER TABLE typhoon_probabilities ADD COLUMN content_sha256 TEXT")


def record_ingestion_run(
    dataset_id: str,
    status: str,
    records_count: int = 0,
    response_time_ms: Optional[float] = None,
    data_timestamp: Optional[str] = None,
    error_message: Optional[str] = None,
) -> int:
    with get_db_connection() as conn:
        cur = conn.execute(
            """INSERT INTO ingestion_runs
               (dataset_id,status,records_count,response_time_ms,data_timestamp,error_message)
               VALUES (?,?,?,?,?,?)""",
            (dataset_id, status, records_count, response_time_ms, data_timestamp, error_message),
        )
        return int(cur.lastrowid)


def record_raw_snapshot(
    dataset_id: str,
    file_path: str,
    content_sha256: str,
    file_size_bytes: int,
    data_timestamp: Optional[str] = None,
) -> int:
    with get_db_connection() as conn:
        cur = conn.execute(
            """INSERT INTO raw_snapshots
               (dataset_id,file_path,content_sha256,file_size_bytes,data_timestamp)
               VALUES (?,?,?,?,?)""",
            (dataset_id, file_path, content_sha256, file_size_bytes, data_timestamp),
        )
        return int(cur.lastrowid)


def get_latest_ingestion_summary() -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(
            """SELECT r.* FROM ingestion_runs r
               JOIN (SELECT dataset_id, MAX(id) AS id FROM ingestion_runs GROUP BY dataset_id) latest
               ON r.id=latest.id ORDER BY r.run_time DESC"""
        ).fetchall()
        return [dict(row) for row in rows]


def get_latest_raw_snapshot_path(dataset_id: str) -> Optional[str]:
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT file_path FROM raw_snapshots WHERE dataset_id=? ORDER BY id DESC LIMIT 1",
            (dataset_id,),
        ).fetchone()
        return str(row[0]) if row else None


def query_rows(sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    """Run an application-owned SELECT and return dictionaries."""
    with get_db_connection() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
