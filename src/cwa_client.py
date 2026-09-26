"""CWA Open Data HTTP client with retries and portable raw snapshots."""
import hashlib
import json
import time
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import (
    BASE_DIR, CWA_API_KEY, CWA_FILE_BASE_URL, CWA_REST_BASE_URL,
    DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT_SEC, RAW_SNAPSHOTS_DIR,
)
from src.database import record_raw_snapshot

Payload = Union[Dict[str, Any], bytes]


class CWAClient:
    """Client for the CWA REST and file APIs."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = (api_key or CWA_API_KEY).strip()
        if not self.api_key:
            raise ValueError("請先在 .env 設定 CWA_API_KEY。")
        self.session = requests.Session()
        retry = Retry(
            total=DEFAULT_MAX_RETRIES,
            connect=DEFAULT_MAX_RETRIES,
            read=DEFAULT_MAX_RETRIES,
            status=DEFAULT_MAX_RETRIES,
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.last_snapshot_paths: Dict[str, str] = {}
        self.last_data_timestamps: Dict[str, Optional[str]] = {}
        self.last_response_times_ms: Dict[str, float] = {}

    @staticmethod
    def _data_timestamp(data: Any) -> Optional[str]:
        if not isinstance(data, dict):
            return None
        preferred = ("IssueTime", "ObsTime", "DateTime", "InitialTime", "StartTime", "sent", "Sent", "Update", "update")
        found: List[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                for key in preferred:
                    item = value.get(key)
                    if isinstance(item, str) and item.strip():
                        found.append(item.strip())
                    elif isinstance(item, dict):
                        visit(item)
                for key, item in value.items():
                    if key not in preferred and isinstance(item, (dict, list)):
                        visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(data)
        # ISO-style timestamps sort chronologically as strings when offsets match.
        return max(found) if found else None

    def _record_snapshot(self, dataset_id: str, content: bytes, extension: str, timestamp: Optional[str]) -> str:
        stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S_%f")
        digest = hashlib.sha256(content).hexdigest()
        filename = f"{dataset_id}_{stamp}_{digest[:8]}.{extension.lower()}"
        path = RAW_SNAPSHOTS_DIR / filename
        path.write_bytes(content)
        relative = path.relative_to(BASE_DIR).as_posix()
        record_raw_snapshot(dataset_id, relative, digest, len(content), timestamp)
        self.last_snapshot_paths[dataset_id] = relative
        return relative

    def _get(self, url: str, dataset_id: str, file_format: str, extra_params: Optional[Dict[str, Any]] = None, save_snapshot: bool = True):
        params: Dict[str, Any] = {"Authorization": self.api_key}
        if extra_params:
            params.update(extra_params)
        started = time.perf_counter()
        response = self.session.get(url, params=params, timeout=DEFAULT_TIMEOUT_SEC)
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.last_response_times_ms[dataset_id] = elapsed_ms
        response.raise_for_status()
        if not response.content:
            raise ValueError("CWA 回傳空內容。")
        if file_format.upper() == "JSON":
            try:
                data = response.json()
            except (requests.exceptions.JSONDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("CWA 回傳內容不是有效 JSON。") from exc
            if isinstance(data, dict) and str(data.get("success", "true")).lower() == "false":
                result = data.get("result")
                result_message = result.get("message") if isinstance(result, dict) else None
                message = result_message or data.get("message") or "CWA API 回報失敗"
                raise ValueError(str(message))
            content = response.content
        elif file_format.upper() == "KMZ":
            content = response.content
            if not zipfile.is_zipfile(BytesIO(content)):
                raise ValueError("CWA 回傳內容不是有效 KMZ/ZIP 檔。")
            data = content
        else:
            content = response.content
            data = content
        if save_snapshot:
            self._record_snapshot(dataset_id, content, "json" if file_format.upper() == "JSON" else file_format, self._data_timestamp(data))
        else:
            self.last_snapshot_paths.pop(dataset_id, None)
        self.last_data_timestamps[dataset_id] = self._data_timestamp(data)
        self.last_response_times_ms[dataset_id] = elapsed_ms
        return data, elapsed_ms

    def fetch_rest_dataset(
        self, dataset_id: str, params: Optional[Dict[str, Any]] = None, save_snapshot: bool = True
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """Fetch a REST datastore JSON payload."""
        self.last_data_timestamps.pop(dataset_id, None)
        self.last_response_times_ms.pop(dataset_id, None)
        try:
            query = dict(params or {})
            query.setdefault("format", "JSON")
            data, elapsed = self._get(f"{CWA_REST_BASE_URL}/{dataset_id}", dataset_id, "JSON", query, save_snapshot)
            return True, data, f"取得成功 ({elapsed:.0f} ms)"
        except (requests.RequestException, ValueError, OSError) as exc:
            return False, None, self._safe_error(exc)

    def fetch_file_dataset(
        self, dataset_id: str, file_format: str = "JSON", save_snapshot: bool = True
    ) -> Tuple[bool, Optional[Payload], str]:
        """Fetch a file API product (JSON or KMZ)."""
        self.last_data_timestamps.pop(dataset_id, None)
        self.last_response_times_ms.pop(dataset_id, None)
        try:
            query = {"downloadType": "WEB", "format": file_format.upper()}
            data, elapsed = self._get(f"{CWA_FILE_BASE_URL}/{dataset_id}", dataset_id, file_format, query, save_snapshot)
            return True, data, f"取得成功 ({elapsed:.0f} ms)"
        except (requests.RequestException, ValueError, OSError) as exc:
            return False, None, self._safe_error(exc)

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        # Never return the request URL: it contains the API key as a query parameter.
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            return f"CWA HTTP {exc.response.status_code}"
        if isinstance(exc, requests.Timeout):
            return "連線逾時，請稍後重試。"
        if isinstance(exc, requests.ConnectionError):
            return "無法連線至 CWA Open Data。"
        return str(exc)[:240]
