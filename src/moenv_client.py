"""HTTP client for the Ministry of Environment open data API."""
import json
import ssl
import time
from typing import Any, Dict, Optional, Tuple

import certifi
import requests
from requests.adapters import HTTPAdapter

from src.config import DEFAULT_TIMEOUT_SEC, MOENV_API_KEY
from src.http_errors import safe_transport_detail

MOENV_AQI_DATASET_ID = "AQX_P_432"
MOENV_AQI_URL = f"https://data.moenv.gov.tw/api/v2/{MOENV_AQI_DATASET_ID}"


class _MOENVTLSAdapter(HTTPAdapter):
    """Keep TLS verification while tolerating a non-strict MOENV cert chain."""

    def __init__(self, *args: Any, **kwargs: Any):
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())
        strict_flag = getattr(ssl, "VERIFY_X509_STRICT", 0)
        if strict_flag:
            # Match the scoped CWA compatibility: retain CA-chain and hostname
            # verification, relaxing only the strict X.509 profile check.
            self.ssl_context.verify_flags &= ~strict_flag
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, connections: int, maxsize: int, block: bool = False, **pool_kwargs: Any):
        pool_kwargs["ssl_context"] = self.ssl_context
        return super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)

    def proxy_manager_for(self, proxy: str, **proxy_kwargs: Any):
        proxy_kwargs["ssl_context"] = self.ssl_context
        return super().proxy_manager_for(proxy, **proxy_kwargs)


class MOENVClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = (api_key if api_key is not None else MOENV_API_KEY).strip()

    def fetch_aqi(self) -> Tuple[Dict[str, Any], float]:
        if not self.api_key:
            raise ValueError("請在 .env 設定 MOENV_API_KEY。")
        started = time.perf_counter()
        session = requests.Session()
        # Limit this compatibility behavior to the MOENV Open Data host.
        session.mount("https://data.moenv.gov.tw/", _MOENVTLSAdapter())
        try:
            response = session.get(
                MOENV_AQI_URL,
                params={"api_key": self.api_key, "format": "JSON", "limit": 1000},
                timeout=DEFAULT_TIMEOUT_SEC,
            )
        except requests.Timeout as exc:
            raise RuntimeError(f"環境部 API 連線逾時（{safe_transport_detail(exc, self.api_key, limit=700)}）") from exc
        except requests.RequestException as exc:
            raise RuntimeError(
                f"無法連線至環境部開放資料平台（{safe_transport_detail(exc, self.api_key, limit=700)}）"
            ) from exc
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
