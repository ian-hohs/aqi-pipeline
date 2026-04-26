"""
config.py
=========
Centralized configuration for the AQI pipeline.

Why a config file?
------------------
In production data engineering, you NEVER hardcode values like API URLs,
file paths, or thresholds inside business logic. Putting them here means:

1. One place to change them when something updates
2. Easy to override in tests (you can monkeypatch this module)
3. Different environments (dev/staging/prod) can swap configs without code changes
"""

import os
from pathlib import Path

# ─── Paths ──────────────────────────────────────────────────────────────────
# Path(__file__) is the location of this file. .parent walks up the tree.
# We resolve the project root so paths work no matter where the script runs from.
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "aqi.db"

# Make sure the data directory exists before anyone tries to write to it
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ─── EPA AirNow API ─────────────────────────────────────────────────────────
# The API requires a free key. We pull it from the environment, never hardcode.
# Why? Hardcoded API keys in Git history are how companies leak credentials.
AIRNOW_API_KEY = os.environ.get("AIRNOW_API_KEY", "")
AIRNOW_BASE_URL = "https://www.airnowapi.org/aq/observation/zipCode/current/"

# A representative set of California zip codes — one per major metro area.
# AirNow returns data for the nearest monitor to each zip.
# In a real production system, you'd query a station catalog and build this
# list dynamically, but a static list is fine for a portfolio project.
CA_ZIP_CODES = [
    "90001",  # Los Angeles
    "94102",  # San Francisco
    "94601",  # Oakland
    "95110",  # San Jose
    "95814",  # Sacramento
    "92101",  # San Diego
    "93701",  # Fresno
    "93301",  # Bakersfield
    "92501",  # Riverside
    "92401",  # San Bernardino
    "93940",  # Monterey
    "95401",  # Santa Rosa
    "94559",  # Napa
    "94901",  # San Rafael (Marin)
    "96001",  # Redding (Shasta)
]


# ─── Alert thresholds ───────────────────────────────────────────────────────
# EPA AQI category breakpoints — these are the official cutoffs.
# https://www.airnow.gov/aqi/aqi-basics/
AQI_THRESHOLDS = {
    "Good":                          (0,   50),
    "Moderate":                      (51,  100),
    "Unhealthy for Sensitive Groups": (101, 150),
    "Unhealthy":                     (151, 200),
    "Very Unhealthy":                (201, 300),
    "Hazardous":                     (301, 500),
}

# We alert on anything above "Moderate" — i.e. AQI > 100
ALERT_THRESHOLD = 100


# ─── Validation rules ───────────────────────────────────────────────────────
# Used by transform.py to reject bad data before it enters the database
AQI_VALID_RANGE = (0, 500)
REQUIRED_FIELDS = {"date", "zip_code", "county", "parameter", "aqi", "category"}


# ─── Logging ────────────────────────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
