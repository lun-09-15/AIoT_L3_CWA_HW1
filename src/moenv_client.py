"""HTTP client for the Ministry of Environment open data API."""
import json
import time
from typing import Any, Dict, Optional, Tuple

import requests

from src.config import DEFAULT_TIMEOUT_SEC, MOENV_API_KEY

MOENV_AQI_DATASET_ID = "AQX_P_432"
MOENV_AQI_URL = f"https://data.moenv.gov.tw/api/v2/{MOENV_AQI_DATASET_ID}"


class MOENVClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = (api_key if api_key is not None else MOENV_API_KEY).strip()

    def fetch_aqi(self) -> Tuple[Dict[str, Any], float]:
        if not self.api_key:
            raise ValueError("請在 .env 設定 MOENV_API_KEY。")
        started = time.perf_counter()
        try:
            response = requests.get(
                MOENV_AQI_URL,
                params={"api_key": self.api_key, "format": "JSON", "limit": 1000},
                timeout=DEFAULT_TIMEOUT_SEC,
            )
        except requests.Timeout as exc:
            raise RuntimeError("環境部 API 連線逾時。") from exc
        except requests.RequestException as exc:
            raise RuntimeError("無法連線至環境部開放資料平台。") from exc
        elapsed_ms = (time.perf_counter() - started) * 1000
        if response.status_code >= 400:
            # Never include the response URL because it contains the API key.
            raise RuntimeError(f"環境部 API HTTP {response.status_code}。請確認 API Key 與呼叫額度。")
        try:
            payload = response.json()
        except (requests.exceptions.JSONDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("環境部 API 回應不是有效 JSON。") from exc
        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError("環境部 API 回報錯誤，請確認 API Key 與資料集代碼。")
        return payload, elapsed_ms
