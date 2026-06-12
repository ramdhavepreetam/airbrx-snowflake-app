# Airbrx Snowflake App — v1

A Snowflake Native App that reads cost and query-history system tables **in-place**
and produces an Airbrx cost-optimization report.

No query text or usage rows leave the account. Only aggregate effectiveness stats
and rule IDs cross the boundary to `api.airbrx.ai`.

```
Customer Snowflake account
┌──────────────────────────────────────────────────────────────┐
│  SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY        ─┐             │
│  SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION    ─┼─► [Task]    │
│  _HISTORY                                     ─┘  every 2h  │
│                                                ▼             │
│                              state.*  (6 Snowflake tables)   │
│                                                │             │
│   [Streamlit in Snowflake] ◄───────────────────┘             │
│        │                                                     │
│        └────► api.airbrx.ai  (rules in / aggregates out)     │
└──────────────────────────────────────────────────────────────┘
```

---

## Install from Snowflake Marketplace

1. Find **Airbrx Cost Intelligence** in the Snowflake Marketplace
2. Click **Get** — the app installs and `setup.sql` runs automatically
3. Grant the requested privileges (ACCOUNT_USAGE, EXECUTE TASK)
4. Bind your Airbrx API key as a secret:
   ```sql
   CREATE SECRET airbrx_api_key
     TYPE = GENERIC_STRING
     SECRET_STRING = '<your-airbrx-api-key>';
   ```
5. Resume the scheduled task:
   ```sql
   ALTER TASK airbrx_cost_intelligence.app_schema.airbrx_analysis_task RESUME;
   ```
6. Open the Streamlit UI from **Apps** in Snowsight

Data begins populating within 2 hours (ACCOUNT_USAGE latency).

---

## Manual / Developer Deploy

### Prerequisites
- [Snowflake CLI](https://docs.snowflake.com/en/developer-guide/snowflake-cli/) installed
- A Snowflake account with ACCOUNTADMIN role

### Steps

```bash
# 1. Authenticate
snow connection add

# 2. Run the app (creates package, uploads to stage, installs)
snow app run

# 3. Open in Snowsight → Apps → Airbrx Cost Intelligence
```

### Create the API key secret
```sql
CREATE SECRET airbrx_api_key
  TYPE = GENERIC_STRING
  SECRET_STRING = '<your-airbrx-api-key>';

ALTER APPLICATION airbrx_cost_intelligence
  SET REFERENCES = ('airbrx_api_key' = airbrx_api_key);
```

### Resume the analysis task
```sql
ALTER TASK airbrx_cost_intelligence.app_schema.airbrx_analysis_task RESUME;
```

### Trigger a manual run
```sql
CALL airbrx_cost_intelligence.app_schema.run_analysis('all');
-- Check result:
SELECT * FROM airbrx_cost_intelligence.state.sync_log ORDER BY ts DESC LIMIT 5;
```

---

## Repo Layout

```
airbrx-snowflake-app/
├── manifest.yml              Native App manifest (privileges, entry points)
├── setup.sql                 Runs at install: tables, task, stored procedure
├── environment.yml           Conda deps for Streamlit-in-Snowflake
├── README.md
├── src/
│   ├── streamlit_app.py      4-screen UI (Coverage, Spend, Savings, Rules)
│   ├── analysis.py           Stored procedure: ingest + recompute + sync
│   └── lib/
│       ├── fingerprint.py    Canonical fingerprint — SHARED CONTRACT with gateway
│       ├── tags.py           abx comment-tag parser
│       ├── queries.py        Parameterized SQL (Snowflake ACCOUNT_USAGE edition)
│       └── airbrx_client.py  Airbrx API client (reads key from Snowflake secret)
├── state/
│   └── ddl.sql               State table definitions (reference copy)
└── tests/
    ├── test_fingerprint.py   32 golden-vector tests (shared with Databricks app)
    └── test_tags.py
```

---

## Key Differences vs Databricks App

| Concern | Databricks | Snowflake |
|---|---|---|
| Cost attribution | Duration-share allocation | Direct `credits_used_compute` per query |
| Query history latency | Real-time | ~45 min (ACCOUNT_USAGE) |
| Task schedule | Hourly | Every 2 hours |
| Auth | WorkspaceClient + OBO | `get_active_session()` — auto |
| Secrets | Secret scopes | `CREATE SECRET` + `SYSTEM$GET_SECRET_VALUE` |
| Admin setup | Manual runbook | Automatic via `setup.sql` at install |

---

## Notes

- **Realized savings require 30+ days of baseline data.** First month shows $0 — expected.
- **ACCOUNT_USAGE has ~45-minute latency.** The task runs every 2 hours to account for this.
- **OBO is not needed.** Streamlit-in-Snowflake auto-authenticates as the current user.
- Fingerprint contract (`v=1`) is identical to the Databricks app — same gateway works for both.
