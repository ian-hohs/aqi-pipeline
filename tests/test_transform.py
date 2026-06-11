"""
test_transform.py
=================
Unit tests for the transformation logic.

WHY UNIT TESTS?
---------------
The transform module is where most bugs hide in data pipelines, because:
  - APIs change their response shape without warning
  - Edge cases (missing fields, weird values) are easy to miss
  - Bugs here corrupt the database silently

Unit tests let you encode the expected behavior and catch regressions
the second they happen. Every engineer writes tests like these.

WHY PYTEST INSTEAD OF UNITTEST?
-------------------------------
Pytest is the modern Python standard. Less boilerplate, better error messages,
and a richer plugin ecosystem. `pip install pytest && pytest tests/` is all
you need to run them.
"""

import pytest

from src.transform import normalize_observation, transform


# ─── Fixtures ───────────────────────────────────────────────────────────────
# A "fixture" in pytest is reusable test setup. Here we define a valid
# raw API response that all our happy-path tests can use.

@pytest.fixture
def valid_raw_record():
    return {
        "DateObserved": "2026-04-26 ",   # trailing space matches real API
        "HourObserved": 14,
        "ReportingArea": "Los Angeles",
        "StateCode": "CA",
        "ParameterName": "PM2.5",
        "AQI": 87,
        "Category": {"Number": 2, "Name": "Moderate"},
        "_query_zip": "90001",
    }


# ─── normalize_observation tests ────────────────────────────────────────────

def test_normalize_valid_record(valid_raw_record):
    """Valid input produces a clean output with all expected fields."""
    result = normalize_observation(valid_raw_record)
    assert result is not None
    assert result["date"] == "2026-04-26"           # trailing space stripped
    assert result["county"] == "Los Angeles"
    assert result["parameter"] == "PM2.5"
    assert result["aqi"] == 87
    assert result["category"] == "Moderate"
    assert result["zip_code"] == "90001"


def test_normalize_drops_record_with_no_aqi(valid_raw_record):
    """A record missing AQI should be dropped (returns None)."""
    valid_raw_record.pop("AQI")
    assert normalize_observation(valid_raw_record) is None


def test_normalize_drops_record_with_invalid_date(valid_raw_record):
    """Bad date format means we can't trust the record — drop it."""
    valid_raw_record["DateObserved"] = "not-a-date"
    assert normalize_observation(valid_raw_record) is None


def test_normalize_drops_aqi_above_legal_range(valid_raw_record):
    """AQI > 500 is sensor error — drop it."""
    valid_raw_record["AQI"] = 999
    assert normalize_observation(valid_raw_record) is None


def test_normalize_drops_negative_aqi(valid_raw_record):
    """Negative AQI is impossible — drop it."""
    valid_raw_record["AQI"] = -5
    assert normalize_observation(valid_raw_record) is None


def test_normalize_handles_missing_category(valid_raw_record):
    """If Category dict is malformed, we fall back to empty string instead of crashing."""
    valid_raw_record["Category"] = {}
    result = normalize_observation(valid_raw_record)
    assert result is not None
    assert result["category"] == ""


# ─── transform() tests ──────────────────────────────────────────────────────

def test_transform_filters_invalid_records(valid_raw_record):
    """A batch with mixed valid/invalid records keeps only the valid ones."""
    invalid = {**valid_raw_record, "AQI": None}
    df = transform([valid_raw_record, invalid])
    assert len(df) == 1
    assert df.iloc[0]["aqi"] == 87


def test_transform_deduplicates_keeping_highest_aqi(valid_raw_record):
    """When same county+date+parameter appears twice, keep the higher AQI."""
    higher = {**valid_raw_record, "AQI": 150}
    df = transform([valid_raw_record, higher])
    assert len(df) == 1
    assert df.iloc[0]["aqi"] == 150  # kept the worse value


def test_transform_handles_empty_input():
    """Empty input list shouldn't crash — returns empty DataFrame."""
    df = transform([])
    assert df.empty


def test_transform_handles_all_invalid_input():
    """If every record is invalid, return empty DataFrame without error."""
    invalid = [{"DateObserved": "garbage"}, {"DateObserved": "also bad"}]
    df = transform(invalid)
    assert df.empty
