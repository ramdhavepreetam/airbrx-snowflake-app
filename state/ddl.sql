-- Airbrx Snowflake App — state table DDL.
-- These are the same 6 tables as the Databricks version, in Snowflake syntax.
-- setup.sql runs this automatically at install; this file is for reference / manual re-run.

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
) CLUSTER BY (d);

CREATE TABLE IF NOT EXISTS state.waterfall_daily (
  d               DATE          NOT NULL,
  warehouse_name  STRING        NOT NULL,
  credits         FLOAT,
  usd             FLOAT,
  layer           STRING,
  updated_at      TIMESTAMP_TZ  NOT NULL
) CLUSTER BY (d);

CREATE TABLE IF NOT EXISTS state.coverage_daily (
  d               DATE         NOT NULL,
  gateway_routed  BIGINT,
  total           BIGINT,
  coverage_ratio  FLOAT,
  updated_at      TIMESTAMP_TZ NOT NULL
) CLUSTER BY (d);

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

CREATE TABLE IF NOT EXISTS state.rules (
  rule_id      STRING       NOT NULL,
  rule_type    STRING,
  description  STRING,
  config_json  VARIANT,
  pulled_at    TIMESTAMP_TZ NOT NULL
);

CREATE TABLE IF NOT EXISTS state.sync_log (
  d             DATE         NOT NULL,
  ts            TIMESTAMP_TZ NOT NULL,
  direction     STRING       NOT NULL,
  payload_bytes BIGINT,
  status        STRING       NOT NULL
) CLUSTER BY (d);
