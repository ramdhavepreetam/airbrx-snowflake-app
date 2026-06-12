# Airbrx Cost Intelligence — Snowflake Native App

Airbrx Cost Intelligence is a **Snowflake Native App** that reads your account's query history and cost data **in-place**, fingerprints repeated query patterns, and produces an actionable cost-optimization report — all without your raw query text or usage rows ever leaving your Snowflake account.

The app communicates only aggregate rule-effectiveness statistics to the Airbrx cloud gateway (`api.airbrx.ai`). No query text, no row-level data, no warehouse IDs cross the network boundary.

---

## How it works

```
Your Snowflake Account
┌──────────────────────────────────────────────────────────────────────┐
│                                                                      │
│  SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY           ──┐                 │
│  SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY ─┼─► [Task]       │
│                                                  ──┘  every 2 hours  │
│                                                          │           │
│                                              ▼                       │
│              state.fingerprint_history   (one row per query)         │
│              state.waterfall_daily       (daily cost by warehouse)   │
│              state.coverage_daily        (gateway coverage %)        │
│              state.rule_effectiveness    (savings per rule)          │
│              state.rules                 (rule definitions)          │
│              state.sync_log              (API call audit trail)      │
│                                              │                       │
│   [Streamlit UI] ◄───────────────────────────┘                       │
│        │                                                             │
│        └────► api.airbrx.ai  (rule IDs + aggregates out / rules in)  │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

**Three-step pipeline (runs every 2 hours):**

1. **Ingest** — reads `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` rows since the last checkpoint, fingerprints each query (SHA-256 of normalised SQL), and writes one row per query to `state.fingerprint_history`. The raw query text is never stored.

2. **Recompute** — rebuilds three aggregate tables:
   - `waterfall_daily` — total credits and USD cost per warehouse per day, sourced from `QUERY_ATTRIBUTION_HISTORY` (direct per-query credit attribution, no duration-share math needed)
   - `coverage_daily` — percentage of queries that were routed through the Airbrx gateway
   - `rule_effectiveness` — per-rule baseline vs observed execution counts, and resulting realized savings in USD

3. **Sync** — calls `GET /v1/rules` to pull the latest Airbrx rule definitions, and `POST /v1/effectiveness` to report aggregate savings back to the Airbrx cloud. Only `rule_id`, `avoided`, and `realized_usd` values are sent — no identifiers, no query text.

---

## What the Streamlit UI shows

| Screen | What you see |
|---|---|
| **Coverage** | Daily % of warehouse traffic routed through the Airbrx gateway. Alerts if untagged spend exceeds 10%. |
| **Redundant Spend** | Top-50 query fingerprints executed more than once, sorted by execution count. Shows total compute time wasted on duplicate work. |
| **Projected vs Realized** | Daily warehouse spend stacked by warehouse (bar chart). Realized savings appear once 30 days of baseline data are available. |
| **Per-Rule Attribution** | Table of every active Airbrx rule: baseline rate, observed executions, avoided executions, and realized savings in USD. |

---

## Install from Snowflake Marketplace

> This is the production path for customers. No CLI or development setup required.

1. Search for **Airbrx Cost Intelligence** in the [Snowflake Marketplace](https://app.snowflake.com/marketplace)
2. Click **Get** — `setup.sql` runs automatically and creates all required objects
3. Grant the requested privileges when prompted:
   - `EXECUTE TASK` — allows the analysis task to run on a schedule
   - `IMPORTED PRIVILEGES ON SNOWFLAKE DB` — read access to `SNOWFLAKE.ACCOUNT_USAGE.*`
4. Create the Airbrx API key secret:
   ```sql
   CREATE OR REPLACE SECRET airbrx_api_key
     TYPE = GENERIC_STRING
     SECRET_STRING = '<your-airbrx-api-key>';
   ```
5. Bind the secret to the app when prompted, or manually:
   ```sql
   CALL airbrx_cost_intelligence.app_schema.register_reference(
     'airbrx_api_key', 'ADD', SYSTEM$REFERENCE('SECRET', 'airbrx_api_key', 'PERSISTENT', 'READ')
   );
   ```
6. Resume the scheduled task:
   ```sql
   ALTER TASK airbrx_cost_intelligence.app_schema.airbrx_analysis_task RESUME;
   ```
7. Open **Apps** in Snowsight and launch **Airbrx Cost Intelligence**

Data begins populating within approximately 2 hours (ACCOUNT_USAGE has a ~45-minute latency; the task runs every 2 hours to account for this).

---

## Developer Setup

### Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11+ | For the local virtual environment |
| Snowflake CLI 3.3.0 | See installation below — avoid 3.20.x (has a known bug) |
| Snowflake account | ACCOUNTADMIN role required for `snow app run` |
| Git | To clone this repo |

### 1. Clone the repo

```bash
git clone https://github.com/ramdhavepreetam/airbrx-snowflake-app.git
cd airbrx-snowflake-app
```

### 2. Create the virtual environment

```bash
python3 -m venv .venv
.venv/bin/pip install "snowflake-cli==3.3.0" "numpy<2"
```

> **Why `numpy<2`?** NumPy 2.x changed its ABI and breaks PyArrow-based packages that Snowflake CLI depends on.
> **Why `snowflake-cli==3.3.0`?** Version 3.20.x has a bug (`'tuple' object has no attribute 'name'`) in feature_flags.py that prevents `snow app run` from completing.

Verify the CLI works:

```bash
.venv/bin/snow --version
# Snowflake CLI version: 3.3.0
```

### 3. Configure a Snowflake connection

```bash
.venv/bin/snow connection add \
  --connection-name dev \
  --account <your-account-id> \
  --user <your-username> \
  --role ACCOUNTADMIN \
  --authenticator snowflake
```

Your account identifier looks like `xy12345.us-east-1`. Find it in Snowsight at the bottom-left corner, or in your Snowflake URL: `https://<account-id>.snowflakecomputing.com`.

For SSO/browser-based authentication, use `--authenticator externalbrowser` instead.

Verify the connection:

```bash
.venv/bin/snow connection test --connection dev
```

### 4. Deploy the app

```bash
.venv/bin/snow app run --connection dev
```

This command:
- Creates an application package in your Snowflake account
- Uploads all source files to a stage
- Installs the app and runs `setup.sql`
- Prints the Snowsight URL when complete

### 5. Create the API key secret

In Snowsight (or via SnowSQL):

```sql
CREATE OR REPLACE SECRET airbrx_api_key
  TYPE = GENERIC_STRING
  SECRET_STRING = '<your-airbrx-api-key>';
```

Then bind it to the app:

```sql
CALL airbrx_cost_intelligence.app_schema.register_reference(
  'airbrx_api_key', 'ADD', SYSTEM$REFERENCE('SECRET', 'airbrx_api_key', 'PERSISTENT', 'READ')
);
```

### 6. Resume the task

```sql
ALTER TASK airbrx_cost_intelligence.app_schema.airbrx_analysis_task RESUME;
```

### 7. Trigger a manual run (optional)

```sql
-- Run the full pipeline immediately (don't wait 2 hours)
CALL airbrx_cost_intelligence.app_schema.run_analysis('all');

-- Check the result
SELECT * FROM airbrx_cost_intelligence.state.sync_log ORDER BY ts DESC LIMIT 5;
```

---

## Running the tests

```bash
python3 -m pytest tests/ -v
```

32 tests covering:
- **Golden-vector fingerprint tests** — pinned expected outputs for known SQL inputs. These are the most critical tests: if any output changes, the fingerprints stored by the gateway will no longer match what this app stores, silently breaking savings attribution.
- **Tag parser tests** — `/* abx v=1 ck=... rule=... route=... */` parsing

---

## Repo layout

```
airbrx-snowflake-app/
├── manifest.yml              Native App manifest — privileges, Streamlit entry point, secret reference
├── setup.sql                 Runs at install: schemas, 6 tables, stored procedure, task, grants
├── snowflake.yml             Snowflake CLI project config (required for snow app run)
├── environment.yml           Conda deps for Streamlit-in-Snowflake runtime
├── README.md                 This file
│
├── src/
│   ├── streamlit_app.py      4-screen Streamlit UI (Coverage, Spend, Savings, Rules)
│   ├── analysis.py           Stored procedure: ingest + recompute + sync
│   └── lib/
│       ├── fingerprint.py    Canonical fingerprint — SHARED CONTRACT with gateway (v=1)
│       ├── tags.py           /* abx ... */ comment-tag parser
│       ├── queries.py        Parameterized SQL (Snowflake ACCOUNT_USAGE edition)
│       └── airbrx_client.py  Airbrx API client (reads key from Snowflake SECRET)
│
├── state/
│   └── ddl.sql               State table definitions — reference copy (setup.sql is authoritative)
│
├── docs/
│   ├── CONTRACT.md           Gateway ↔ app fingerprint contract (locked at v=1)
│   ├── DEPLOYMENT.md         Step-by-step deployment guide with troubleshooting
│   └── MARKETPLACE.md        Snowflake Marketplace submission checklist
│
└── tests/
    ├── test_fingerprint.py   32 golden-vector tests (shared with Databricks app)
    └── test_tags.py
```

---

## Key design decisions

### Why `QUERY_ATTRIBUTION_HISTORY` instead of `METERING_HISTORY`?

`QUERY_ATTRIBUTION_HISTORY` provides **direct credit attribution per `query_id`**. This means we can join it to `QUERY_HISTORY` on `query_id` and get the exact cost of each individual query — no duration-share allocation, no warehouse utilization math. The Databricks version had to do complex pro-rata credit calculations; this version simply sums `credits_used_compute`.

### Why every 2 hours instead of hourly?

`SNOWFLAKE.ACCOUNT_USAGE` has an approximately 45-minute latency. Running every hour means the first run after a batch of queries would see incomplete data and miss some rows. Running every 2 hours ensures data is fully settled before ingestion.

### Why is query text never stored?

The fingerprint (`ck`) is a one-way SHA-256 hash. Given a fingerprint, you cannot recover the original SQL. This is intentional: customers should not need to trust Airbrx with their actual query text, and the Airbrx cloud never needs it to compute savings.

### Why is the fingerprint algorithm a "shared contract"?

The Airbrx gateway prepends a `/* abx v=1 ck=<hash> rule=... route=... */` comment to every query it forwards. The hash in that comment is computed by the gateway using the same algorithm as `fingerprint.py`. When this app ingests the query from ACCOUNT_USAGE, it recomputes the fingerprint from the query text — after stripping the `/* abx */` comment — and gets the same `ck`. This is how gateway actions are linked to ACCOUNT_USAGE rows.

If either side changes the algorithm, the `ck` values stop matching and savings attribution breaks silently. The algorithm is locked at `VERSION = 1` and any change requires bumping `VERSION` and coordinating with the gateway.

---

## Differences vs the Databricks app

| Concern | Databricks App | Snowflake App |
|---|---|---|
| Cost attribution | Duration-share allocation (complex) | Direct `credits_used_compute` per query |
| Query history latency | Real-time | ~45 minutes (ACCOUNT_USAGE) |
| Task schedule | Hourly | Every 2 hours |
| Auth inside app | WorkspaceClient + OBO token | `get_active_session()` — automatic |
| Secrets | Databricks secret scopes | `CREATE SECRET` + `SYSTEM$GET_SECRET_VALUE` |
| Setup | Manual runbook | Automatic via `setup.sql` at install |
| Fingerprint contract | v=1 | v=1 — identical, same gateway works |
| Client header | `X-Client: databricks-app-v1` | `X-Client: snowflake-app-v1` |

---

## Troubleshooting

**Coverage shows 0% even after data appears**

The gateway must be routing queries through Airbrx. Coverage measures the fraction of queries in `QUERY_HISTORY` whose `query_text` starts with `/* abx`. If coverage is 0%, the Airbrx gateway is not yet connected to your Snowflake warehouses.

**`sync_log` shows `error: 401`**

Your `airbrx_api_key` secret contains an invalid or expired API key. Update it:
```sql
ALTER SECRET airbrx_api_key SET SECRET_STRING = '<new-key>';
```

**Realized savings show $0 / "Pending baseline"**

Realized savings require 30+ days of `fingerprint_history` data to establish a pre-window baseline. The first month always shows $0 — this is expected.

**`CALL run_analysis('all')` returns `ERROR: ...`**

Check `state.sync_log` for the most recent error. Common causes:
- Invalid API key (401) — update the secret
- Network rule not applied — ensure `airbrx_api_access` EAI is active
- ACCOUNT_USAGE not yet visible — the `IMPORTED PRIVILEGES ON SNOWFLAKE DB` grant may still be propagating (can take a few minutes after install)

**`snow app run` fails with `'tuple' object has no attribute 'name'`**

You're running Snowflake CLI 3.20.x which has a known bug. Downgrade:
```bash
.venv/bin/pip install "snowflake-cli==3.3.0" --force-reinstall
```

---

## Security

- No query text is ever stored or transmitted
- The only outbound network call is to `api.airbrx.ai:443` (HTTPS), declared in the manifest as a network rule
- The API key is stored in a Snowflake `SECRET` object and accessed via `SYSTEM$GET_SECRET_VALUE` — it never appears in query text or logs
- The Streamlit UI uses `get_active_session()` — it inherits the current user's Snowflake permissions and requires no separate auth config

---

## License

Copyright © 2025 Airbrx, Inc. All rights reserved.
