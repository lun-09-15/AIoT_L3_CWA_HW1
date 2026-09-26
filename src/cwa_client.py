"""
CWA API Client module.
Provides a shared HTTP fetcher for CWA Open Data REST API and File API endpoints,
with timeout handling, retry mechanism, raw snapshot persistence, and DB run logging.
"""

import time
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import (
    CWA_API_KEY,
    CWA_REST_BASE_URL,
    CWA_FILE_BASE_URL,
    RAW_SNAPSHOTS_DIR,
    DEFAULT_TIMEOUT_SEC,
    DEFAULT_MAX_RETRIES,
)
from src.database import record_ingestion_run, record_raw_snapshot

class CWAClient:
    """Shared HTTP client for CWA Open Data."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or CWA_API_KEY
        if not self.api_key:
            raise ValueError(
                "CWA API Key is missing! Please configure CWA_API_KEY in your .env file."
            )

        self.session = requests.Session()
        retries = Retry(
            total=DEFAULT_MAX_RETRIES,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def fetch_rest_dataset(
        self,
        dataset_id: str,
        params: Optional[Dict[str, Any]] = None,
        save_snapshot: bool = True
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Fetches dataset from CWA REST API: /api/v1/rest/datastore/{dataset_id}
        Returns: (success: bool, data: Optional[dict], message: str)
        """
        url = f"{CWA_REST_BASE_URL}/{dataset_id}"
        query_params = {"Authorization": self.api_key}
        if params:
            query_params.update(params)

        start_time = time.perf_counter()
        try:
            resp = self.session.get(url, params=query_params, timeout=DEFAULT_TIMEOUT_SEC)
            elapsed_ms = (time.perf_counter() - start_time) * 1000

            if resp.status_code != 200:
                err_msg = f"HTTP {resp.status_code}: {resp.text[:150]}"
                record_ingestion_run(dataset_id, status="FAILED", response_time_ms=elapsed_ms, error_message=err_msg)
                return False, None, err_msg

            data = resp.json()
            if not data.get("success") == "true":
                err_msg = f"CWA API returned failure: {data.get('message', 'Unknown error')}"
                record_ingestion_run(dataset_id, status="FAILED", response_time_ms=elapsed_ms, error_message=err_msg)
                return False, None, err_msg

            # Save snapshot if requested
            if save_snapshot:
                self._save_raw_snapshot(dataset_id, resp.content, extension="json")

            record_ingestion_run(dataset_id, status="SUCCESS", response_time_ms=elapsed_ms)
            return True, data, "Success"

        except requests.exceptions.RequestException as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            err_msg = f"Network or request error: {str(e)}"
            record_ingestion_run(dataset_id, status="FAILED", response_time_ms=elapsed_ms, error_message=err_msg)
            return False, None, err_msg
        except json.JSONDecodeError as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            err_msg = f"JSON decode error: {str(e)}"
            record_ingestion_run(dataset_id, status="FAILED", response_time_ms=elapsed_ms, error_message=err_msg)
            return False, None, err_msg

    def fetch_file_dataset(
        self,
        dataset_id: str,
        file_format: str = "JSON",
        save_snapshot: bool = True
    ) -> Tuple[bool, Optional[Union[Dict[str, Any], bytes]], str]:
        """
        Fetches dataset from CWA File API: /fileapi/v1/opendataapi/{dataset_id}
        file_format: 'JSON' or 'KMZ'
        Returns: (success: bool, data_or_bytes: Optional[dict|bytes], message: str)
        """
        url = f"{CWA_FILE_BASE_URL}/{dataset_id}"
        query_params = {
            "Authorization": self.api_key,
            "downloadType": "WEB",
            "format": file_format
        }

        start_time = time.perf_counter()
        try:
            resp = self.session.get(url, params=query_params, timeout=DEFAULT_TIMEOUT_SEC)
            elapsed_ms = (time.perf_counter() - start_time) * 1000

            if resp.status_code != 200:
                err_msg = f"HTTP {resp.status_code}: {resp.text[:150]}"
                record_ingestion_run(dataset_id, status="FAILED", response_time_ms=elapsed_ms, error_message=err_msg)
                return False, None, err_msg

            content_bytes = resp.content

            if file_format.upper() == "JSON":
                # Ensure correct UTF-8 decoding
                json_data = json.loads(content_bytes.decode("utf-8", errors="replace"))
                if save_snapshot:
                    self._save_raw_snapshot(dataset_id, content_bytes, extension="json")
                record_ingestion_run(dataset_id, status="SUCCESS", response_time_ms=elapsed_ms)
                return True, json_data, "Success"
            else:
                # Binary files like KMZ
                if save_snapshot:
                    self._save_raw_snapshot(dataset_id, content_bytes, extension=file_format.lower())
                record_ingestion_run(dataset_id, status="SUCCESS", response_time_ms=elapsed_ms)
                return True, content_bytes, "Success"

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            err_msg = f"File fetch error: {str(e)}"
            record_ingestion_run(dataset_id, status="FAILED", response_time_ms=elapsed_ms, error_message=err_msg)
            return False, None, err_msg

    def _save_raw_snapshot(self, dataset_id: str, content: bytes, extension: str = "json") -> Path:
        """Saves raw snapshot to disk and indexes in SQLite."""
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{dataset_id}_{timestamp_str}.{extension}"
        file_path = RAW_SNAPSHOTS_DIR / filename

        with open(file_path, "wb") as f:
            f.write(content)

        sha256_hash = hashlib.sha256(content).hexdigest()
        record_raw_snapshot(
            dataset_id=dataset_id,
            file_path=str(file_path),
            content_sha256=sha256_hash,
            file_size_bytes=len(content),
            data_timestamp=datetime.now().isoformat()
        )
        return file_path
