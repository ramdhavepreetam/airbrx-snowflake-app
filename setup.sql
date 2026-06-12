-- Airbrx Snowflake Native App — setup script.
-- Runs automatically when a customer installs the app from the Marketplace.
-- Creates all state tables, the analysis stored procedure, and the scheduled task.

-- ============================================================
-- 1. Application schemas
-- ============================================================
CREATE OR ALTER VERSIONED SCHEMA app_schema;
CREATE SCHEMA IF NOT EXISTS state;
CREATE SCHEMA IF NOT EXISTS ui;

-- ============================================================
-- 2. External network access rule (api.airbrx.ai)
--    Network rule is schema-scoped; EAI is granted by account admin post-install.
-- ============================================================
CREATE OR REPLACE NETWORK RULE app_schema.airbrx_api_rule
  MODE       = EGRESS
  TYPE       = HOST_PORT
  VALUE_LIST = ('api.airbrx.ai:443');

-- ============================================================
-- 3. State tables
-- ============================================================

-- One row per finished query that passed through fingerprinting.
-- Raw query_text is NEVER stored — only the hash (ck).
CREATE TABLE IF NOT EXISTS state.fingerprint_history (
  ck                  STRING        NOT NULL,
  query_id            STRING        NOT NULL,
  d                   DATE          NOT NULL,
  start_time          TIMESTAMP_TZ,
  warehouse_name      STRING,
  total_elapsed_time  BIGINT,
  route               STRING,
  rule_id             STRING,
  ingested_at         TIMESTAMP_TZ  NOT NULL
)
CLUSTER BY (d);

-- Daily aggregate: total cost by warehouse and day.
CREATE TABLE IF NOT EXISTS state.waterfall_daily (
  d               DATE          NOT NULL,
  warehouse_name  STRING        NOT NULL,
  credits         FLOAT,
  usd             FLOAT,
  layer           STRING,
  updated_at      TIMESTAMP_TZ  NOT NULL
)
CLUSTER BY (d);

-- Daily coverage: gateway-routed vs total query counts.
CREATE TABLE IF NOT EXISTS state.coverage_daily (
  d               DATE         NOT NULL,
  gateway_routed  BIGINT,
  total           BIGINT,
  coverage_ratio  FLOAT,
  updated_at      TIMESTAMP_TZ NOT NULL
)
CLUSTER BY (d);

-- Per-fingerprint realized savings, updated daily.
CREATE TABLE IF NOT EXISTS state.rule_effectiveness (
  ck            STRING       NOT NULL,
  rule_id       STRING,
  window        STRING       NOT NULL,
  baseline_rate FLOAT,
  observed      BIGINT,
  avoided       BIGINT,
  realized_usd  FLOAT,
  updated_at    TIMESTAMP_TZ NOT NULL
);

-- Airbrx rule definitions pulled from api.airbrx.ai.
CREATE TABLE IF NOT EXISTS state.rules (
  rule_id      STRING       NOT NULL,
  rule_type    STRING,
  description  STRING,
  config_json  VARIANT,
  pulled_at    TIMESTAMP_TZ NOT NULL
);

-- Outbound API call audit trail.
CREATE TABLE IF NOT EXISTS state.sync_log (
  d             DATE         NOT NULL,
  ts            TIMESTAMP_TZ NOT NULL,
  direction     STRING       NOT NULL,
  payload_bytes BIGINT,
  status        STRING       NOT NULL
)
CLUSTER BY (d);

-- ============================================================
-- 4. Analysis stored procedure (no external access until EAI is granted)
-- ============================================================
CREATE OR REPLACE PROCEDURE app_schema.run_analysis(mode STRING)
  RETURNS STRING
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.10'
  PACKAGES = ('snowflake-snowpark-python', 'requests')
  HANDLER = 'analysis.main'
  IMPORTS = ('/src/analysis.py',
             '/src/lib/fingerprint.py',
             '/src/lib/tags.py',
             '/src/lib/airbrx_client.py');

-- ============================================================
-- 5. Scheduled task
--    Created post-install by the account admin (warehouse name varies per account):
--    CREATE TASK state.airbrx_analysis_task
--      SCHEDULE = 'USING CRON 0 */2 * * * UTC'
--      WAREHOUSE = <your_warehouse>
--    AS CALL airbrx_cost_intelligence.app_schema.run_analysis('all');
--    ALTER TASK state.airbrx_analysis_task RESUME;
-- ============================================================

-- ============================================================
-- 6. Streamlit UI
-- ============================================================
CREATE OR REPLACE STREAMLIT ui.main_app
  FROM 'src'
  MAIN_FILE = 'streamlit_app.py';

-- ============================================================
-- 7. App role + grants
-- ============================================================
CREATE APPLICATION ROLE IF NOT EXISTS app_public;

GRANT USAGE ON SCHEMA state                              TO APPLICATION ROLE app_public;
GRANT SELECT ON ALL TABLES IN SCHEMA state               TO APPLICATION ROLE app_public;
GRANT USAGE ON SCHEMA app_schema                         TO APPLICATION ROLE app_public;
GRANT USAGE ON PROCEDURE app_schema.run_analysis(STRING) TO APPLICATION ROLE app_public;
GRANT USAGE ON SCHEMA ui                                 TO APPLICATION ROLE app_public;
GRANT USAGE ON STREAMLIT ui.main_app                     TO APPLICATION ROLE app_public;
