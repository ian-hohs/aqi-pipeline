"""
ingest.py
=========
Pulls current air quality observations from the EPA AirNow REST API.

WHY THIS FILE EXISTS SEPARATELY:
-------------------------------
In data engineering, we separate "ingestion" (getting data IN) from
"transformation" (cleaning data UP) and "loading" (putting data OUT).

This is called the ETL pattern: Extract, Transform, Load.

Why bother? Because each stage has different failure modes:
  - Ingestion fails when APIs are down or credentials expire
  - Transformation fails when data shape changes
  - Loading fails when the database is locked or full

Separating them means when something breaks, you know EXACTLY where to look.
You can also test each stage in isolation, swap implementations (e.g. switch
from AirNow to a different data source), and re-run failed stages without
re-running the whole pipeline.
"""

import logging
import time
from typing import List, Dict, Any

import requests

from src import config

# Get a logger named after this module — best practice for traceability
log = logging.getLogger(__name__)


def fetch_zip_observations(zip_code: str, api_key: str, retries: int = 3) -> List[Dict[str, Any]]:
    """
    Pull current AQI observations for a single zip code from EPA AirNow.

    WHY RETRIES?
    -----------
    Public APIs are flaky. They timeout, return 503s during deployments, or
    rate-limit you temporarily. A pipeline that gives up after one failed
    request is fragile. Retrying with backoff is a basic resiliency pattern
    every production system uses.

    Parameters
    ----------
    zip_code : str  - 5-digit US zip code
    api_key  : str  - EPA AirNow API key
    retries  : int  - how many times to retry before giving up

    Returns
    -------
    List of observation dicts. Empty list if no data or all retries failed.
    """
    params = {
        "format":   "application/json",
        "zipCode":  zip_code,
        "distance": 25,        # Search within 25 miles for a monitor
        "API_KEY":  api_key,
    }

    for attempt in range(1, retries + 1):
        try:
            # 30s timeout — never let a request hang forever
            r = requests.get(config.AIRNOW_BASE_URL, params=params, timeout=30)
            r.raise_for_status()      # raises an exception on 4xx/5xx responses
            return r.json() or []     # API returns [] when no monitor nearby
        except requests.exceptions.RequestException as e:
            log.warning(f"  zip {zip_code} attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                # Exponential backoff: wait 1s, then 2s, then 4s, etc.
                # This gives the API time to recover instead of hammering it.
                time.sleep(2 ** (attempt - 1))

    log.error(f"  zip {zip_code} failed after {retries} attempts")
    return []


def fetch_all_california(api_key: str = None) -> List[Dict[str, Any]]:
    """
    Pull AQI observations for every California zip code in our list.

    Returns one flat list — each observation has its zip code attached so
    downstream stages can group/filter by location.
    """
    api_key = api_key or config.AIRNOW_API_KEY
    if not api_key:
        # Fail loudly and early. A pipeline that silently uses no API key
        # would just produce empty data, which is much harder to debug.
        raise ValueError(
            "AIRNOW_API_KEY not set. Get a free key at "
            "https://docs.airnowapi.org/account/request/ and set it as an "
            "environment variable."
        )

    all_observations = []
    log.info(f"Fetching observations for {len(config.CA_ZIP_CODES)} zip codes...")

    for zip_code in config.CA_ZIP_CODES:
        observations = fetch_zip_observations(zip_code, api_key)
        # Tag each observation with the zip we queried for, since the API
        # response doesn't always echo it back consistently
        for obs in observations:
            obs["_query_zip"] = zip_code
        all_observations.extend(observations)

    log.info(f"Pulled {len(all_observations)} total observations from EPA AirNow")
    return all_observations
