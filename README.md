# Air Quality Alert Pipeline 🌫️

**An automated data pipeline that pulls California air quality data from the EPA every day, validates it, stores it in a database, and logs alerts when air quality crosses EPA "unhealthy" thresholds.**

Runs entirely on GitHub Actions — no server required.

---

## What it does

Every day at 13:00 UTC (6am Pacific), this pipeline:

1. **Ingests** today's air quality data from EPA AirNow API for every California county with active monitoring stations
2. **Validates** the data — drops records with missing fields, flags outliers, ensures AQI values are in the legal range (0–500)
3. **Loads** the cleaned data into a SQLite database, deduplicating against existing records
4. **Detects alerts** — flags any county where AQI exceeds 100 (Unhealthy for Sensitive Groups)
5. **Logs alerts** to a separate `alerts` table with timestamp and severity
6. **Commits the updated database back to the repo** so you have a complete history

If anything fails — API down, schema change, network error — the GitHub Action fails loudly and you get an email from GitHub.

---

## Why this project exists

Built to learn production data engineering patterns:

- **ETL pipeline design** — separate ingestion, transformation, and loading stages
- **Scheduled pipelines** — GitHub Actions as a cron-like scheduler
- **Data quality checks** — validating data before it enters the database
- **Idempotency** — running the pipeline twice produces the same result
- **Error handling** — pipeline fails gracefully and reports failures
- **Logging** — structured logs at every stage for debugging
- **Testing** — unit tests for the transformation logic with `pytest`

---

## Architecture

```
       ┌──────────────────────────┐
       │  GitHub Actions (cron)   │  ← runs daily at 13:00 UTC
       └────────────┬─────────────┘
                    │
                    ▼
          ┌──────────────────┐
          │   ingest.py      │  ← pulls EPA AirNow API (per CA zipcode)
          └────────┬─────────┘
                   │  raw JSON
                   ▼
          ┌──────────────────┐
          │  transform.py    │  ← validates, normalizes, deduplicates
          └────────┬─────────┘
                   │  clean DataFrame
                   ▼
          ┌──────────────────┐
          │     load.py      │  ← UPSERT into SQLite
          └────────┬─────────┘
                   │
                   ▼
          ┌──────────────────┐
          │    alerts.py     │  ← scans for AQI > 100, writes alerts table
          └──────────────────┘
```

---

## Stack

- **Python 3.11** — core language
- **requests** — REST API calls to EPA
- **pandas** — data transformation
- **SQLite** (via `sqlite3` stdlib) — storage, no separate DB server needed
- **GitHub Actions** — scheduling and execution
- **pytest** — unit testing
- **EPA AirNow API** — live air quality readings (free, requires API key)

---

## Running locally

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/aqi-pipeline.git
cd aqi-pipeline
pip install -r requirements.txt
```

### 2. Get an EPA AirNow API key

Free, takes 30 seconds: https://docs.airnowapi.org/account/request/

### 3. Set the key as an environment variable

**Mac/Linux:**
```bash
export AIRNOW_API_KEY=your-key-here
```

**Windows PowerShell:**
```powershell
$env:AIRNOW_API_KEY="your-key-here"
```

### 4. Run the pipeline

```bash
python -m src.pipeline
```

You'll see logs like:
```
2026-04-26 13:00:01 INFO Starting pipeline run
2026-04-26 13:00:02 INFO Ingest: pulled 47 records from EPA AirNow
2026-04-26 13:00:02 INFO Transform: 45 valid records after validation
2026-04-26 13:00:02 INFO Load: inserted 12 new records (33 duplicates skipped)
2026-04-26 13:00:02 INFO Alerts: 3 counties exceed AQI threshold of 100
2026-04-26 13:00:02 INFO Pipeline complete in 1.4s
```

### 5. Run the tests

```bash
pytest tests/
```

---

## Querying the results

After a few days of runs, you'll have history. Open the database:

```bash
sqlite3 data/aqi.db
```

```sql
-- All readings
SELECT * FROM readings ORDER BY date DESC LIMIT 10;

-- Alerts in the last 7 days
SELECT * FROM alerts WHERE alert_date >= date('now', '-7 days');

-- Counties with most alerts
SELECT county, COUNT(*) as alert_count
FROM alerts
GROUP BY county
ORDER BY alert_count DESC;
```

---

## Deployment to GitHub Actions

1. Push this repo to GitHub
2. Go to your repo → Settings → Secrets and variables → Actions
3. Add a new repository secret: `AIRNOW_API_KEY` with your EPA key as the value
4. The pipeline runs automatically on the schedule defined in `.github/workflows/pipeline.yml`
5. You can also trigger it manually from the Actions tab

---

## Project structure

```
aqi-pipeline/
├── README.md
├── requirements.txt
├── .github/
│   └── workflows/
│       └── pipeline.yml      # GitHub Actions schedule
├── src/
│   ├── __init__.py
│   ├── ingest.py             # EPA AirNow API client
│   ├── transform.py          # Validation + cleaning
│   ├── load.py               # SQLite UPSERT
│   ├── alerts.py             # Threshold detection
│   ├── pipeline.py           # Orchestrates the full run
│   └── config.py             # Constants and settings
├── tests/
│   ├── test_transform.py     # Unit tests for transformation logic
│   └── test_alerts.py        # Unit tests for alert detection
└── data/
    └── aqi.db                # SQLite database (created on first run)
```

---

## Author

Ian Hohsfield — built as part of a learning portfolio targeting data engineering roles.
