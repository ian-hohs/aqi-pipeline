"""
alerts.py
=========
Scans the readings table for any AQI values that exceed our alert threshold,
and writes them to a separate `alerts` table.

WHY A SEPARATE ALERTS TABLE?
----------------------------
Could we just query the readings table whenever we want to know "what alerts
fired today?" Yes. So why bother writing to a separate table?

A few reasons:
  1. AUDIT TRAIL — the alerts table is a permanent log of what crossed the
     threshold and when. If the threshold logic changes later, the historical
     alerts are still preserved.
  2. PERFORMANCE — querying a small alerts table is faster than scanning
     millions of readings rows.
  3. DECOUPLING — downstream consumers (dashboards, notifications, reports)
     can subscribe to the alerts table without knowing about the readings
     schema.

This is a common pattern: raw data → derived/aggregated tables. In big
data systems this is sometimes called a "medallion architecture" with
bronze (raw), silver (cleaned), and gold (aggregated) layers.
"""

import logging
import sqlite3

from src import config
from src.load import get_connection

log = logging.getLogger(__name__)


def severity_for_aqi(aqi: int) -> str:
    """Map a numeric AQI to its EPA category name."""
    for label, (lo, hi) in config.AQI_THRESHOLDS.items():
        if lo <= aqi <= hi:
            return label
    return "Unknown"


def detect_and_record_alerts(db_path=None, threshold: int = None) -> int:
    """
    Find any readings above the threshold and write them to alerts table.

    Uses INSERT OR IGNORE so re-running doesn't create duplicate alerts
    for the same (date, county, parameter).

    Returns the number of NEW alerts created (existing ones are skipped).
    """
    threshold = threshold if threshold is not None else config.ALERT_THRESHOLD

    with get_connection(db_path) as conn:
        # Find every reading above the threshold
        rows = conn.execute(
            """
            SELECT date, county, parameter, aqi, category
            FROM readings
            WHERE aqi > ?
            """,
            (threshold,),
        ).fetchall()

        if not rows:
            log.info(f"Alerts: 0 readings exceed AQI threshold of {threshold}")
            return 0

        # Build alert records with computed severity
        alert_rows = [
            (date, county, parameter, aqi, category, severity_for_aqi(aqi))
            for (date, county, parameter, aqi, category) in rows
        ]

        # INSERT OR IGNORE: if this exact alert (date+county+parameter) already
        # exists in the alerts table, skip it. This makes the operation idempotent.
        before = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        conn.executemany(
            """
            INSERT OR IGNORE INTO alerts (alert_date, county, parameter, aqi, category, severity)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            alert_rows,
        )
        after = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]

    new_alerts = after - before
    log.info(
        f"Alerts: {len(rows)} readings above threshold of {threshold}, "
        f"{new_alerts} new alerts recorded"
    )
    return new_alerts
