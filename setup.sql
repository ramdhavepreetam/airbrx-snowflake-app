-- Airbrx Snowflake Native App — setup script.
-- Runs automatically when a customer installs the app from the Marketplace.
-- Creates all state tables, the analysis stored procedure, and the scheduled task.

-- ============================================================
-- 1. Application schema
-- ============================================================
CREATE SCHEMA IF NOT EXISTS app_schema;
CREATE SCHEMA IF NOT EXISTS state;

-- ============================================================
-- 2. External network access rule (api.airbrx.ai)
-- ============================================================
CREATE OR REPLACE NETWORK RULE airbrx_api_rule
  MODE         = EGRESS
  TYPE         = HOST_PORT
  VALUE_LIST   = ('api.airbrx.ai:443');

CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION airbrx_api_access
  ALLOWED_NETWORK_RULES = (airbrx_api_rule)
  ALLOWED_AUTHENTICATION_SECRETS = (reference('airbrx_api_key'))
  ENABLED = TRUE;

-- ============================================================
-- 3. State tables
-- ============================================================

-- Hourly ingest: one row per finished query that passed through fingerprinting.
-- Raw query_text is NEVER stored — only the hash (ck).
CREATE TABLE IF NOT EXISTS state.fingerprint_history (
  ck                  STRING        NOT NULL COMMENT 'Canonical query fingerprint (§6c)',
  query_id            STRING        NOT NULL,
  d                   DATE          NOT NULL COMMENT 'Partition date = DATE(start_time)',
  start_time          TIMESTAMP_TZ,
  warehouse_name      STRING,
  total_elapsed_time  BIGINT        COMMENT 'Milliseconds',
  route               STRING        COMMENT 'warehouse | cache_miss | smaller_wh | bypass | NULL',
  rule_id             STRING        COMMENT 'Airbrx rule that matched, if any',
  ingested_at         TIMESTAMP_TZ  NOT NULL
)
CLUSTER BY (d);

-- Daily aggregate: total cost by warehouse and day.
CREATE TABLE IF NOT EXISTS state.waterfall_daily (
  d               DATE          NOT NULL,
  warehouse_name  STRING        NOT NULL,
  credits         FLOAT,
  usd             FLOAT,
  layer           STRING        COMMENT 'raw | projected_saving | realized_saving',
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
  window        STRING       NOT NULL COMMENT 'ISO date range: YYYY-MM-DD/YYYY-MM-DD',
  baseline_rate FLOAT        COMMENT 'executions per day in pre-window',
  observed      BIGINT       COMMENT 'actual executions in current window',
  avoided       BIGINT       COMMENT 'max(0, baseline_rate * window_len - observed)',
  realized_usd  FLOAT,
  updated_at    TIMESTAMP_TZ NOT NULL
);

-- Airbrx rule definitions pulled from api.airbrx.ai.
CREATE TABLE IF NOT EXISTS state.rules (
  rule_id      STRING       NOT NULL,
  rule_type    STRING       COMMENT 'cache | bypass | smaller_wh',
  description  STRING,
  config_json  VARIANT      COMMENT 'Rule config as JSON',
  pulled_at    TIMESTAMP_TZ NOT NULL
);

-- Outbound API call audit trail.
CREATE TABLE IF NOT EXISTS state.sync_log (
  d             DATE         NOT NULL,
  ts            TIMESTAMP_TZ NOT NULL,
  direction     STRING       NOT NULL COMMENT 'push_pull | pull_only | push_only',
  payload_bytes BIGINT,
  status        STRING       NOT NULL COMMENT 'ok | error: <message>'
)
CLUSTER BY (d);

-- ============================================================
-- 4. Analysis stored procedure
-- ============================================================
CREATE OR REPLACE PROCEDURE app_schema.run_analysis(mode STRING)
  RETURNS STRING
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python', 'requests')
  HANDLER = 'analysis.main'
  IMPORTS = ('/src/analysis.py',
             '/src/lib/fingerprint.py',
             '/src/lib/tags.py',
             '/src/lib/airbrx_client.py')
  EXTERNAL_ACCESS_INTEGRATIONS = (airbrx_api_access)
  SECRETS = ('airbrx_api_key' = reference('airbrx_api_key'));

-- ============================================================
-- 5. Scheduled task (every 2 hours — ACCOUNT_USAGE has 45-min latency)
-- ============================================================
CREATE TASK IF NOT EXISTS app_schema.airbrx_analysis_task
  SCHEDULE = 'USING CRON 0 */2 * * * UTC'
  USER_TASK_MANAGED_INITIAL_WAREHOUSE_SIZE = 'XSMALL'
AS
  CALL app_schema.run_analysis('all');

-- Tasks start suspended; customer resumes after setup.
-- ALTER TASK app_schema.airbrx_analysis_task RESUME;

-- ============================================================
-- 6. Reference callback (for API key binding)
-- ============================================================
CREATE OR REPLACE PROCEDURE app_schema.register_reference(
  ref_name STRING, operation STRING, ref_or_alias STRING
)
  RETURNS STRING
  LANGUAGE SQL
AS $$
  BEGIN
    CASE operation
      WHEN 'ADD' THEN
        SELECT SYSTEM$SET_REFERENCE(:ref_name, :ref_or_alias);
      WHEN 'REMOVE' THEN
        SELECT SYSTEM$REMOVE_REFERENCE(:ref_name);
      WHEN 'CLEAR' THEN
        SELECT SYSTEM$REMOVE_REFERENCE(:ref_name);
    END CASE;
    RETURN 'done';
  END;
$$;

-- ============================================================
-- 7. App role grants
-- ============================================================
GRANT USAGE ON SCHEMA state           TO APPLICATION ROLE app_public;
GRANT SELECT ON ALL TABLES IN SCHEMA state TO APPLICATION ROLE app_public;
GRANT USAGE ON SCHEMA app_schema      TO APPLICATION ROLE app_public;
GRANT USAGE ON PROCEDURE app_schema.run_analysis(STRING) TO APPLICATION ROLE app_public;
