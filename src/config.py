"""
Configuration module for CWA Weather Dashboard.
Loads environment variables and defines system paths and constants.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Base Directory: root of the project
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file
load_dotenv(BASE_DIR / ".env")

def _load_api_key(name: str) -> str:
    """Read Streamlit Secrets first, then environment variables/.env."""
    # Prefer the app-specific Cloud value if both Secrets and an environment
    # variable are configured; otherwise a stale environment value can mask it.
    try:
        import streamlit as st

        value = str(st.secrets.get(name, "")).strip()
        if value:
            return value
    except (ImportError, FileNotFoundError, RuntimeError):
        # Local runs without configured Streamlit Secrets should use .env.
        pass

    return os.getenv(name, "").strip()


# Keep provider keys separate. Never print or persist these values.
CWA_API_KEY = _load_api_key("CWA_API_KEY")
MOENV_API_KEY = _load_api_key("MOENV_API_KEY")
CWA_REST_BASE_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
CWA_FILE_BASE_URL = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi"

# Storage paths
DATA_DIR = BASE_DIR / "data"
RAW_SNAPSHOTS_DIR = DATA_DIR / "snapshots"
DB_PATH = DATA_DIR / "weather_dashboard.db"

# Ensure essential directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
RAW_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# Default HTTP Settings
DEFAULT_TIMEOUT_SEC = 8
DEFAULT_MAX_RETRIES = 1
DEFAULT_RETRY_DELAY_SEC = 2
