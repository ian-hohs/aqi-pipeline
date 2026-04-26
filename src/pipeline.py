"""
pipeline.py
===========
Orchestrates the full ETL pipeline: ingest → transform → load → alert.

This is the entry point. Run with: `python -m src.pipeline`

WHY A SEPARATE ORCHESTRATOR FILE?
---------------------------------
The other files (ingest, transform, load, alerts) each do ONE thing well.
This file just coordinates them — it doesn't contain any business logic itself.

This separation matters because:
  1. Each stage can be tested independently
  2. You can swap orchestrators (e.g. move from a Python script to Airflow,
     Dagster, or Prefect later) without changing the underlying logic
  3. Reading this file tells you the WHOLE pipeline at a glance — high-level
     structure without getting lost in implementation details

In real production systems, the orchestrator is usually a separate tool
(Airflow, Dagster, etc.) that handles dependencies, retries, alerting,
and parallelism across many pipelines. We're doing the bare minimum here,
but the structure is the same.
"""

import logging
import sys
import time

from src import config, ingest, transform, load, alerts, sheets_sync


def setup_logging():
    """
    Configure root logger for the whole pipeline.

    WHY DO THIS HERE?
    ----------------
    Each module gets its own logger via `logging.getLogger(__name__)`,
    but they all inherit format and level from the root config set here.
    Setting it once at the entry point keeps logging consistent everywhere.
    """
    logging.basicConfig(
        level=logging.INFO,
        format=config.LOG_FORMAT,
        datefmt=config.LOG_DATE_FORMAT,
        stream=sys.stdout,  # GitHub Actions captures stdout for the run logs
    )


def run() -> int:
    """
    Run the full pipeline. Returns exit code (0 = success, 1 = failure).

    WHY RETURN AN EXIT CODE?
    -----------------------
    GitHub Actions watches the exit code of the script. If we return non-zero,
    GitHub marks the workflow as failed and emails you. Without this, a broken
    pipeline could fail silently for weeks.
    """
    setup_logging()
    log = logging.getLogger("pipeline")

    log.info("=" * 60)
    log.info("Starting AQI pipeline run")
    log.info("=" * 60)
    start = time.time()

    try:
        # ─── Stage 1: Ingest ────────────────────────────────────────────
        raw = ingest.fetch_all_california()
        if not raw:
            log.warning("No raw data returned — pipeline complete with no work to do")
            return 0

        # ─── Stage 2: Transform ─────────────────────────────────────────
        clean_df = transform.transform(raw)
        if clean_df.empty:
            log.warning("All raw records failed validation — nothing to load")
            return 0

        # ─── Stage 3: Load ──────────────────────────────────────────────
        load.upsert_readings(clean_df)

        # ─── Stage 4: Alert detection ───────────────────────────────────
        alerts.detect_and_record_alerts()

        # ─── Stage 5: Google Sheets sync ────────────────────────────────
        # Wrapped in its own try/except so that a sheets failure doesn't
        # fail the whole pipeline. The DB is the source of truth — if we
        # fail to update the spreadsheet, the data still lives in SQLite
        # and the next run can re-sync it.
        try:
            sheets_sync.sync_to_sheets()
        except Exception as e:
            log.warning(f"Sheets sync failed (non-fatal): {e}")

        elapsed = time.time() - start
        log.info(f"Pipeline complete in {elapsed:.1f}s")
        return 0

    except Exception as e:
        # Top-level catch — log the failure and exit non-zero.
        # exc_info=True includes the full traceback in the log output,
        # which is essential for debugging from GitHub Actions logs.
        log.error(f"Pipeline FAILED: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(run())
