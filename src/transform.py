"""
transform.py
============
Validates and normalizes raw API responses into clean, database-ready records.

WHY VALIDATION MATTERS:
----------------------
Bad data is worse than no data. If you let bad records into your database,
every downstream report, dashboard, or analysis becomes wrong. And once bad
data is in, finding and fixing it is painful.

This is called "garbage in, garbage out" — and the only fix is to validate
data BEFORE it enters the database.

In this file, we:
  1. Drop records missing required fields
  2. Clamp AQI values to the legal 0–500 range
  3. Normalize date formats
  4. Convert types (strings to ints where needed)
  5. Log every record we drop, so we can investigate later

Every drop is logged because silent data loss is one of the worst bugs in
data engineering — you don't know it's happening until something downstream
breaks weeks later.
"""

import logging
from datetime import datetime
from typing import List, Dict, Any

import pandas as pd

from src import config

log = logging.getLogger(__name__)


def normalize_observation(raw: Dict[str, Any]) -> Dict[str, Any] | None:
    """
    Convert one raw EPA API response into our clean schema.

    Returns None if the record is invalid — the caller filters Nones out.

    EPA's API returns records that look like:
        {
          "DateObserved": "2026-04-26 ",        # note trailing space!
          "HourObserved": 14,
          "LocalTimeZone": "PST",
          "ReportingArea": "Los Angeles",
          "StateCode": "CA",
          "Latitude": 34.05,
          "Longitude": -118.24,
          "ParameterName": "PM2.5",
          "AQI": 87,
          "Category": {"Number": 2, "Name": "Moderate"}
        }

    We normalize this into:
        {
          "date":      "2026-04-26",
          "zip_code":  "90001",
          "county":    "Los Angeles",
          "parameter": "PM2.5",
          "aqi":       87,
          "category":  "Moderate",
        }
    """
    try:
        # The DateObserved field has a trailing space — real-world data is messy
        date_str = raw.get("DateObserved", "").strip()
        # Validate it parses as a date — wrong format = drop the record
        datetime.strptime(date_str, "%Y-%m-%d")

        aqi = raw.get("AQI")
        if aqi is None:
            return None

        aqi = int(aqi)

        # Clamp to legal range. AQI values outside 0–500 are sensor errors.
        # We could DROP these, but clamping is more forgiving for portfolio demos.
        # In a stricter production system you'd drop them and alert.
        lo, hi = config.AQI_VALID_RANGE
        if aqi < lo or aqi > hi:
            log.warning(f"  AQI {aqi} out of range [{lo}, {hi}] — dropping record")
            return None

        category = raw.get("Category", {}).get("Name", "")

        return {
            "date":      date_str,
            "zip_code":  raw.get("_query_zip", ""),
            "county":    raw.get("ReportingArea", "").strip(),
            "parameter": raw.get("ParameterName", "").strip(),
            "aqi":       aqi,
            "category":  category,
        }

    except (ValueError, TypeError, KeyError) as e:
        # Catch ANY parse error — bad date format, missing key, wrong type.
        # We log and return None so the bad record gets filtered out cleanly.
        log.warning(f"  Failed to normalize record: {e}")
        return None


def transform(raw_observations: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    Take a list of raw EPA API responses and return a validated DataFrame.

    Why pandas?
    -----------
    For 100 rows, lists of dicts work fine. But the moment you want to
    deduplicate, group, sort, or do bulk operations, pandas is the right tool.
    It's also what the loader expects.
    """
    log.info(f"Transforming {len(raw_observations)} raw records...")

    # List comprehension with filter — cleaner than a for-loop with append
    cleaned = [
        normalized for obs in raw_observations
        if (normalized := normalize_observation(obs)) is not None
    ]

    if not cleaned:
        log.warning("Transform produced 0 valid records — all input was invalid")
        return pd.DataFrame()

    df = pd.DataFrame(cleaned)

    # Deduplicate within this batch — same county can have multiple monitors
    # reporting the same pollutant. We keep the highest AQI (worst case).
    before = len(df)
    df = (
        df.sort_values("aqi", ascending=False)
          .drop_duplicates(subset=["date", "county", "parameter"], keep="first")
          .reset_index(drop=True)
    )
    deduped = before - len(df)

    log.info(f"Transform: {len(df)} valid records ({deduped} duplicates removed)")
    return df
