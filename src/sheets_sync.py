"""
sheets_sync.py
==============
Pushes pipeline data to a Google Sheet after every successful run.

WHY THIS EXISTS:
----------------
A database is great for queries, but most stakeholders (managers, analysts,
non-engineers) don't know SQL and won't open a .db file. They live in
spreadsheets.

A common data engineering pattern is:
    raw data -> database (single source of truth)
              -> spreadsheet (consumer-facing view, auto-refreshed)

The database stays the system of record. The spreadsheet is just a
"materialized view" — a snapshot regenerated on a schedule.

WHY GOOGLE SHEETS API INSTEAD OF gspread?
-----------------------------------------
gspread is a popular wrapper but adds an extra dependency. The official
google-api-python-client does the same job with one less library to maintain.

HOW AUTH WORKS:
---------------
We use a Google Cloud "service account" — a non-human Google account that
exists just to authenticate APIs. Its credentials are stored as JSON. We
share the target spreadsheet WITH the service account's email address,
the same way you'd share a sheet with a coworker.

The credentials JSON is loaded from an environment variable so it never
gets committed to Git. In GitHub Actions, we'll inject it from a secret.
"""

import json
import logging
import os
from datetime import datetime, timezone

import pandas as pd
from google.oauth2 import service_account
from googleapiclient.discovery import build

from src import config
from src.load import get_connection

log = logging.getLogger(__name__)


# Google Sheets API scope — read AND write to spreadsheets.
# Without this scope, our credentials get permission-denied errors.
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def get_sheets_service():
    """
    Build an authenticated Google Sheets API client.

    Reads service account credentials from the GOOGLE_CREDENTIALS_JSON
    environment variable (a stringified JSON blob).
    """
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise ValueError(
            "GOOGLE_CREDENTIALS_JSON env var not set. "
            "See README for Google Cloud setup instructions."
        )

    # Parse the JSON string into a dict, then build credentials from it.
    # Wrapped in try/except to give a clearer error if the JSON is malformed.
    try:
        creds_info = json.loads(creds_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"GOOGLE_CREDENTIALS_JSON is not valid JSON: {e}")

    credentials = service_account.Credentials.from_service_account_info(
        creds_info, scopes=SCOPES
    )

    # cache_discovery=False prevents a noisy warning in serverless environments
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def fetch_recent_data(days: int = 30) -> pd.DataFrame:
    """
    Pull the last N days of readings from the database for the spreadsheet.

    Why limit by days? Because a Google Sheet with 10,000 rows is unusable.
    We show the recent window — anyone wanting full history can query the DB.
    """
    with get_connection() as conn:
        df = pd.read_sql_query(
            f"""
            SELECT
                date,
                county,
                parameter,
                aqi,
                category,
                zip_code,
                inserted_at
            FROM readings
            WHERE date >= date('now', '-{days} days')
            ORDER BY date DESC, aqi DESC
            """,
            conn,
        )
    log.info(f"Fetched {len(df)} readings from last {days} days")
    return df


def fetch_recent_alerts(days: int = 30) -> pd.DataFrame:
    """Pull recent alerts for a separate sheet tab."""
    with get_connection() as conn:
        df = pd.read_sql_query(
            f"""
            SELECT
                alert_date,
                county,
                parameter,
                aqi,
                category,
                severity,
                raised_at
            FROM alerts
            WHERE alert_date >= date('now', '-{days} days')
            ORDER BY alert_date DESC, aqi DESC
            """,
            conn,
        )
    log.info(f"Fetched {len(df)} alerts from last {days} days")
    return df


def write_to_sheet(service, spreadsheet_id: str, sheet_name: str, df: pd.DataFrame):
    """
    Replace the contents of a sheet tab with the contents of a DataFrame.

    The strategy is "wipe and rewrite" rather than "append":
      - Easier to reason about than diffing
      - Idempotent — running twice produces the same result
      - Tradeoff: no in-place comments survive (acceptable for a data export)

    For very large datasets you'd want incremental updates, but for our
    rolling 30-day window, wipe-and-rewrite is the right pattern.
    """
    if df.empty:
        log.warning(f"No data to write to '{sheet_name}' — skipping")
        return

    # Convert DataFrame to a 2D list with header row first.
    # Google Sheets API expects values in this format: [[row1], [row2], ...]
    header = [df.columns.tolist()]
    rows = df.fillna("").astype(str).values.tolist()
    values = header + rows

    # Step 1: clear the entire tab so old data doesn't bleed through
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1:Z100000",
    ).execute()

    # Step 2: write the new values starting at A1
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",  # don't try to interpret formulas
        body={"values": values},
    ).execute()

    log.info(f"Wrote {len(df)} rows to sheet '{sheet_name}'")


def write_metadata_sheet(service, spreadsheet_id: str, readings_count: int, alerts_count: int):
    """
    Update a small 'Status' tab showing when the sheet was last updated.

    This is a small touch but matters in real systems — stakeholders need to
    know "is this data fresh?" at a glance. A timestamp at the top answers that.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    values = [
        ["Field", "Value"],
        ["Last updated", timestamp],
        ["Readings (last 30 days)", str(readings_count)],
        ["Alerts (last 30 days)", str(alerts_count)],
        ["Pipeline status", "OK"],
        ["", ""],
        ["Source", "EPA AirNow API"],
        ["Pipeline repo", "https://github.com/ian-hohs/aqi-pipeline"],
    ]

    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id, range="Status!A1:Z100"
    ).execute()

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range="Status!A1",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()
    log.info("Updated Status tab")


def sync_to_sheets(spreadsheet_id: str = None):
    """
    Top-level function: pull fresh data from DB and push to all sheet tabs.

    Called by pipeline.py after the load+alerts stages complete.
    """
    spreadsheet_id = spreadsheet_id or os.environ.get("GOOGLE_SHEET_ID")
    if not spreadsheet_id:
        log.warning(
            "GOOGLE_SHEET_ID not set — skipping sheets sync. "
            "Set the env var to enable Google Sheets export."
        )
        return

    log.info("Starting Google Sheets sync...")
    service = get_sheets_service()

    readings = fetch_recent_data(days=30)
    alerts = fetch_recent_alerts(days=30)

    write_to_sheet(service, spreadsheet_id, "Readings", readings)
    write_to_sheet(service, spreadsheet_id, "Alerts", alerts)
    write_metadata_sheet(service, spreadsheet_id, len(readings), len(alerts))

    log.info("Google Sheets sync complete")
