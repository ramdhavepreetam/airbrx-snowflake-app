# Deployment Guide

This guide covers everything needed to go from a fresh clone to a running Airbrx Cost Intelligence app, including developer testing with `snow app run`, first-run validation, and production-readiness checklist.

---

## Prerequisites checklist

Before starting, confirm you have:

- [ ] A Snowflake account where you have the `ACCOUNTADMIN` role (required for `CREATE APPLICATION PACKAGE`, `CREATE EXTERNAL ACCESS INTEGRATION`, and granting privileges)
- [ ] Python 3.11 or later installed locally (`python3 --version`)
- [ ] Git (`git --version`)
- [ ] Your Snowflake account identifier (find it in Snowsight bottom-left, or in the URL `https://<account-id>.snowflakecomputing.com`)
- [ ] An Airbrx API key (from app.airbrx.ai → Settings → API Keys)

---

## Part 1 — Local developer deploy (`snow app run`)

### Step 1: Clone and enter the repo

```bash
git clone https://github.com/ramdhavepreetam/airbrx-snowflake-app.git
cd airbrx-snowflake-app
```

### Step 2: Create a Python virtual environment

```bash
python3 -m venv .venv
.venv/bin/pip install "snowflake-cli==3.3.0" "numpy<2"
```

Verify:

```bash
.venv/bin/snow --version
# Expected: Snowflake CLI version: 3.3.0
```

> **Important:** Do not use `snowflake-cli==3.20.x`. It has a known bug (`'tuple' object has no attribute 'name'` in `feature_flags.py`) that prevents `snow app run` from completing. Use `3.3.0` until this is resolved upstream.

### Step 3: Add a Snowflake connection

Run this command and follow the prompts:

```bash
.venv/bin/snow connection add
```

Or pass all values as flags (non-interactive):

```bash
# Password auth
.venv/bin/snow connection add \
  --connection-name dev \
  --account <account-id> \
  --user <username> \
  --password <password> \
  --role ACCOUNTADMIN \
  --warehouse <warehouse-name>

# SSO / browser auth (recommended for human users)
.venv/bin/snow connection add \
  --connection-name dev \
  --account <account-id> \
  --user <username> \
  --role ACCOUNTADMIN \
  --authenticator externalbrowser
```

Test it:

```bash
.venv/bin/snow connection test --connection dev
# Expected: Connection 'dev' is valid
```

### Step 4: Deploy with `snow app run`

```bash
.venv/bin/snow app run --connection dev
```

What this does:
1. Reads `snowflake.yml` to find the manifest and artifact list
2. Uploads everything under `src/`, `setup.sql`, `manifest.yml`, and `environment.yml` to a stage in an application package named `airbrx_cost_intelligence_pkg`
3. Creates or upgrades the application `airbrx_cost_intelligence`
4. Runs `setup.sql` which creates all schemas, tables, the stored procedure, and the scheduled task
5. Prints the Snowsight URL on success

Expected output (abbreviated):

```
Uploading artifacts...
Creating application package 'airbrx_cost_intelligence_pkg'...
Installing application 'airbrx_cost_intelligence'...
Running setup.sql...
Application installed successfully.
Open in Snowsight: https://app.snowflake.com/...
```

### Step 5: Create and bind the API key secret

In Snowsight or SnowSQL, using the same Snowflake account:

```sql
-- Create the secret in your account (outside the app)
CREATE OR REPLACE SECRET airbrx_api_key
  TYPE = GENERIC_STRING
  SECRET_STRING = '<your-airbrx-api-key>';

-- Bind it to the app
CALL airbrx_cost_intelligence.app_schema.register_reference(
  'airbrx_api_key',
  'ADD',
  SYSTEM$REFERENCE('SECRET', 'airbrx_api_key', 'PERSISTENT', 'READ')
);
```

### Step 6: Resume the task

The task is installed in a suspended state to give you time to complete setup before it starts running.

```sql
ALTER TASK airbrx_cost_intelligence.app_schema.airbrx_analysis_task RESUME;
```

Confirm it's running:

```sql
SHOW TASKS IN APPLICATION airbrx_cost_intelligence;
-- STATE column should show: started
```

### Step 7: Trigger a manual first run

Don't wait 2 hours — trigger it manually to validate the pipeline:

```sql
CALL airbrx_cost_intelligence.app_schema.run_analysis('all');
```

Check the result:

```sql
-- Was the API call successful?
SELECT ts, status, payload_bytes
FROM airbrx_cost_intelligence.state.sync_log
ORDER BY ts DESC
LIMIT 5;

-- Were fingerprints ingested?
SELECT COUNT(*), MAX(d) AS latest_date
FROM airbrx_cost_intelligence.state.fingerprint_history;

-- Is there cost data?
SELECT d, warehouse_name, ROUND(usd, 2) AS usd
FROM airbrx_cost_intelligence.state.waterfall_daily
ORDER BY d DESC
LIMIT 10;
```

### Step 8: Open the Streamlit UI

In Snowsight: **Data** → **Apps** → **Airbrx Cost Intelligence** → **Launch app**

Or use the URL printed by `snow app run`.

---

## Part 2 — Teardown / re-deploy

To completely remove the app and start fresh:

```sql
DROP APPLICATION airbrx_cost_intelligence CASCADE;
DROP APPLICATION PACKAGE airbrx_cost_intelligence_pkg CASCADE;
```

Then re-run `snow app run`.

To upgrade an existing install without dropping:

```bash
.venv/bin/snow app run --connection dev
```

`snow app run` is idempotent — it upgrades if the app already exists.

---

## Part 3 — Running the test suite

Tests do not require a Snowflake connection. They test the fingerprint algorithm and tag parser in isolation.

```bash
python3 -m pytest tests/ -v
```

Expected output: `32 passed`

These tests are the most critical part of CI. The fingerprint golden vectors pin the exact output of the hashing algorithm. If any golden vector fails:
- Do not change the expected value
- Do not push the change
- The algorithm in `fingerprint.py` must be changed and `VERSION` must be bumped
- The gateway team must coordinate the bump

---

## Part 4 — Environment variables (optional overrides)

The stored procedure (`analysis.py`) reads two optional environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `AIRBRX_CREDIT_RATE_USD` | `3.00` | Snowflake list price per credit in USD. Override if you have negotiated pricing. |
| `AIRBRX_API_URL` | `https://api.airbrx.ai` | API base URL. Override for staging/testing. |

To set these for a manual run:

```sql
-- Temporarily override the credit rate
ALTER PROCEDURE airbrx_cost_intelligence.app_schema.run_analysis(STRING)
  SET ENVIRONMENT_VARIABLES = ('AIRBRX_CREDIT_RATE_USD=2.75');
```

---

## Part 5 — Validating each pipeline mode independently

Run individual modes to isolate issues:

```sql
-- Only ingest new rows from QUERY_HISTORY
CALL airbrx_cost_intelligence.app_schema.run_analysis('ingest');

-- Only recompute aggregates (waterfall, coverage, rule_effectiveness)
CALL airbrx_cost_intelligence.app_schema.run_analysis('recompute');

-- Only sync with api.airbrx.ai (push effectiveness, pull rules)
CALL airbrx_cost_intelligence.app_schema.run_analysis('sync');
```

---

## Part 6 — Troubleshooting

### `snow app run` fails with NumPy ABI error

```
ValueError: numpy.dtype size changed, may indicate binary incompatibility
```

Fix: pin `numpy<2` in the virtual environment.

```bash
.venv/bin/pip install "numpy<2" --force-reinstall
```

### `snow app run` fails with `'tuple' object has no attribute 'name'`

You have `snowflake-cli==3.20.x`. Downgrade:

```bash
.venv/bin/pip install "snowflake-cli==3.3.0" "numpy<2" --force-reinstall
```

### `run_analysis` returns `ERROR: 401`

The API key secret is invalid or missing.

```sql
-- Check the secret exists
SHOW SECRETS LIKE 'airbrx_api_key';

-- Update with the correct key
ALTER SECRET airbrx_api_key SET SECRET_STRING = '<new-key>';
```

### `run_analysis` returns `ERROR: name 'fingerprint' is not defined`

The IMPORTS clause in `setup.sql` is not loading the lib files correctly. Check that `setup.sql` uses `/src/lib/fingerprint.py` (with leading `/`) not `@stage/path` format. Re-deploy with `snow app run`.

### Coverage shows 0% permanently

The gateway is not routing queries through Airbrx, or the gateway is not connected to this Snowflake account. Coverage is measured by counting queries in `QUERY_HISTORY` whose `query_text` starts with `/* abx`. If the gateway is correctly configured, check that queries from that warehouse appear in `QUERY_HISTORY` with the prefix.

### Realized savings show $0 after 30+ days

Check whether `state.rule_effectiveness` has a populated `avoided` column:

```sql
SELECT ck, rule_id, baseline_rate, observed, avoided, realized_usd
FROM airbrx_cost_intelligence.state.rule_effectiveness
ORDER BY realized_usd DESC NULLS LAST
LIMIT 10;
```

If `avoided = 0` for all rows: the gateway has not prevented any repeated queries yet (the baseline rate equals or exceeds the observed rate). This is a usage pattern question, not a bug.

If the table is empty: recompute has not run yet, or fingerprint_history has less than 60 days of data (the baseline window is days -60 to -31).

### Task is not running automatically

```sql
-- Check task status
SHOW TASKS IN APPLICATION airbrx_cost_intelligence;

-- If STATE = suspended, resume it
ALTER TASK airbrx_cost_intelligence.app_schema.airbrx_analysis_task RESUME;

-- Check last run time and status
SELECT *
FROM TABLE(INFORMATION_SCHEMA.TASK_HISTORY(
  TASK_NAME => 'AIRBRX_ANALYSIS_TASK',
  SCHEDULED_TIME_RANGE_START => DATEADD('hour', -24, CURRENT_TIMESTAMP())
))
ORDER BY SCHEDULED_TIME DESC;
```

---

## Part 7 — Upgrading the app version

When a new version is released on the Marketplace, customers upgrade from Snowsight. For developer deploys:

```bash
git pull origin master
.venv/bin/snow app run --connection dev
```

`snow app run` will upgrade in-place. Existing state tables are preserved (all tables use `CREATE TABLE IF NOT EXISTS`).

---

## Useful SQL references

```sql
-- Count fingerprints by date
SELECT d, COUNT(*) AS rows
FROM airbrx_cost_intelligence.state.fingerprint_history
GROUP BY d ORDER BY d DESC LIMIT 30;

-- Top warehouses by cost (last 30 days)
SELECT warehouse_name, ROUND(SUM(usd), 2) AS total_usd
FROM airbrx_cost_intelligence.state.waterfall_daily
WHERE layer = 'raw'
GROUP BY 1 ORDER BY 2 DESC;

-- Coverage trend
SELECT d, ROUND(coverage_ratio * 100, 1) AS pct
FROM airbrx_cost_intelligence.state.coverage_daily
ORDER BY d DESC LIMIT 14;

-- Rules pulled from Airbrx
SELECT rule_id, rule_type, description, pulled_at
FROM airbrx_cost_intelligence.state.rules
ORDER BY pulled_at DESC;

-- Full sync log
SELECT ts, direction, status, payload_bytes
FROM airbrx_cost_intelligence.state.sync_log
ORDER BY ts DESC LIMIT 20;
```
