"""
test_alerts.py
==============
Tests for the alert detection logic.

Uses a TEMPORARY in-memory database for each test so tests don't interfere
with each other or pollute the real database. This is a critical pattern:
NEVER let tests touch your real data.
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from src import alerts, load


@pytest.fixture
def temp_db():
    """
    Create a fresh empty database for each test, delete it after.

    pytest's `tmp_path` fixture would work too, but tempfile gives us
    a single .db file path which matches how SQLite expects to be used.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    yield db_path
    db_path.unlink(missing_ok=True)


def insert_reading(db_path, date, county, parameter, aqi, category):
    """Helper: insert one reading into the test database."""
    load.init_schema(db_path)
    with load.get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO readings (date, county, parameter, aqi, category) VALUES (?, ?, ?, ?, ?)",
            (date, county, parameter, aqi, category),
        )


def test_severity_mapping():
    """AQI numbers map to the correct EPA category names."""
    assert alerts.severity_for_aqi(25) == "Good"
    assert alerts.severity_for_aqi(75) == "Moderate"
    assert alerts.severity_for_aqi(125) == "Unhealthy for Sensitive Groups"
    assert alerts.severity_for_aqi(175) == "Unhealthy"
    assert alerts.severity_for_aqi(250) == "Very Unhealthy"
    assert alerts.severity_for_aqi(400) == "Hazardous"


def test_no_alerts_when_aqi_below_threshold(temp_db):
    """If no readings exceed threshold, no alerts should be created."""
    insert_reading(temp_db, "2026-04-26", "Marin", "PM2.5", 45, "Good")
    n = alerts.detect_and_record_alerts(db_path=temp_db, threshold=100)
    assert n == 0


def test_alert_created_for_high_aqi(temp_db):
    """A reading above threshold creates one alert."""
    insert_reading(temp_db, "2026-04-26", "Fresno", "PM2.5", 175, "Unhealthy")
    n = alerts.detect_and_record_alerts(db_path=temp_db, threshold=100)
    assert n == 1

    # Verify the alert was actually written with correct severity
    with load.get_connection(temp_db) as conn:
        rows = conn.execute("SELECT county, aqi, severity FROM alerts").fetchall()
    assert len(rows) == 1
    assert rows[0] == ("Fresno", 175, "Unhealthy")


def test_alerts_are_idempotent(temp_db):
    """Running alert detection twice doesn't create duplicate alerts."""
    insert_reading(temp_db, "2026-04-26", "Kern", "PM2.5", 160, "Unhealthy")

    first_run = alerts.detect_and_record_alerts(db_path=temp_db, threshold=100)
    second_run = alerts.detect_and_record_alerts(db_path=temp_db, threshold=100)

    assert first_run == 1   # first time creates the alert
    assert second_run == 0  # second time, no NEW alerts created

    # Verify only one alert exists in the table
    with load.get_connection(temp_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    assert count == 1


def test_threshold_is_configurable(temp_db):
    """Passing a different threshold changes which readings trigger alerts."""
    insert_reading(temp_db, "2026-04-26", "Sonoma", "PM2.5", 75, "Moderate")

    # AQI 75 doesn't exceed default threshold of 100
    n_default = alerts.detect_and_record_alerts(db_path=temp_db, threshold=100)
    assert n_default == 0

    # But it does exceed a stricter threshold of 50
    n_strict = alerts.detect_and_record_alerts(db_path=temp_db, threshold=50)
    assert n_strict == 1
