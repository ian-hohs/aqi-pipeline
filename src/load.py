"""
load.py
=======
Loads cleaned data into SQLite using an UPSERT (insert-or-update) pattern.

WHY UPSERT INSTEAD OF JUST INSERT?
----------------------------------
If we just INSERT, then re-running the pipeline tomorrow would create duplicate
rows for any record that already exists. UPSERT means: "if this row already
exists (based on the primary key), update it; otherwise insert it."

This makes the pipeline IDEMPOTENT — a fancy word meaning "running it twice
gives the same result as running it once." Idempotency is a fundamental
property of well-designed data pipelines because:

  - Network blips might cause the pipeline to run twice on the same data
  - You might want to manually re-run a day's pipeline to fix a bug
  - GitHub Actions might retry a failed job

Without idempotency, any of these scenarios corrupts your data.

WHY SQLITE INSTEAD OF POSTGRES?
-------------------------------
For a portfolio project, SQLite is perfect:
  - Zero setup (it's just a file)
  - Ships with Python's standard library
  - Easy for reviewers to inspect (just open the .db file)
  - Same SQL syntax as Postgres for 95% of operations

For real production data, you'd swap this for Postgres or a data warehouse
like Snowflake/BigQuery/Databricks. The pipeline structure stays the same.
"""

import logging
import sqlite3
from contextlib import contextmanager

import pandas as pd

from src import config

log = logging.getLogger(__name__)


# ─── Schema ─────────────────────────────────────────────────────────────────
# Single source of truth for what our tables look like. Defining this in code
# (rather than running it manually in a SQL client once) means:
#   1. Schema is version-controlled with the rest of the code
#   2. New developers can spin up a fresh DB by just running the pipeline
#   3. CI/CD can recreate the DB from scratch for testing

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    date       TEXT NOT NULL,
    county     TEXT NOT NULL,
    parameter  TEXT NOT NULL,
    aqi        INTEGER NOT NULL,
    category   TEXT NOT NULL,
    zip_code   TEXT,
    inserted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, county, parameter)
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_date TEXT NOT NULL,
    county     TEXT NOT NULL,
    parameter  TEXT NOT NULL,
    aqi        INTEGER NOT NULL,
    category   TEXT NOT NULL,
    severity   TEXT NOT NULL,
    raised_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (alert_date, county, parameter)
);

CREATE INDEX IF NOT EXISTS idx_readings_date ON readings(date);
CREATE INDEX IF NOT EXISTS idx_alerts_date ON alerts(alert_date);
"""


@contextmanager
def get_connection(db_path=None):
    """
    Context manager for safe database connections.

    WHY A CONTEXT MANAGER?
    ----------------------
    Database connections are a resource — like an open file. If you forget
    to close them, you leak resources. If your code crashes mid-query,
    you might leave the database in a weird state.

    Using `with` syntax guarantees the connection is committed and closed,
    even if something inside the `with` block throws an exception.
    """
    db_path = db_path or config.DB_PATH
    conn = sqlite3.connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(db_path=None):
    """Create tables if they don't exist. Safe to call repeatedly."""
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)
    log.debug("Schema initialized")


def upsert_readings(df: pd.DataFrame, db_path=None) -> int:
    """
    Insert new readings, replacing duplicates based on the primary key.

    Returns the number of rows that were actually new (vs duplicates skipped).

    HOW THE UPSERT WORKS:
    --------------------
    SQLite's `INSERT OR REPLACE` syntax does the following:
      - If no row exists with the given (date, county, parameter), insert it
      - If a row already exists, replace it (keeps the latest AQI value)

    For this pipeline that's the right behavior: if EPA later corrects an AQI
    reading, our next pipeline run picks up the corrected value automatically.
    """
    if df.empty:
        log.warning("No records to load")
        return 0

    init_schema(db_path)

    with get_connection(db_path) as conn:
        # Count existing rows BEFORE the insert so we can report new vs replaced
        before_count = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]

        # to_sql with `if_exists='append'` would create duplicates — we want UPSERT.
        # Build parameterized query manually so we get full control.
        rows = df[["date", "county", "parameter", "aqi", "category", "zip_code"]].values.tolist()
        conn.executemany(
            """
            INSERT OR REPLACE INTO readings (date, county, parameter, aqi, category, zip_code)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

        after_count = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]

    new_rows = after_count - before_count
    replaced = len(df) - new_rows
    log.info(f"Load: inserted {new_rows} new records ({replaced} duplicates updated)")
    return new_rows
