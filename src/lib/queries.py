"""
Core SQL queries — Snowflake ACCOUNT_USAGE edition.

Key schema facts:
  SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
    query_id, query_text, user_name, warehouse_name, start_time, end_time,
    total_elapsed_time (ms), execution_status ('SUCCESS'|'FAIL'|'INCIDENT'),
    query_text (string — the /* abx ... */ comment)

  SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY
    query_id, credits_used_compute  — direct cost per query (no duration-share needed)

  SNOWFLAKE.ACCOUNT_USAGE.METERING_HISTORY
    service_name (warehouse_name), credits_used_compute, start_time, end_time
"""

LOOKBACK_DAYS = 30
CREDIT_RATE_USD = 3.00  # default list price $/credit — override via env var

# §5a — priced usage by warehouse and day (simpler than Databricks — direct credits)
WATERFALL = """
SELECT
  DATE_TRUNC('DAY', a.start_time)::DATE             AS d,
  h.warehouse_name,
  SUM(a.credits_used_compute)                       AS credits,
  SUM(a.credits_used_compute) * {credit_rate_usd}   AS usd
FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY a
JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY h USING (query_id)
WHERE a.start_time >= DATEADD('day', -{lookback_days}, CURRENT_DATE())
  AND h.warehouse_name IS NOT NULL
GROUP BY 1, 2
ORDER BY 1
"""

# §5b — per-fingerprint attributed cost (direct from QUERY_ATTRIBUTION_HISTORY)
ATTRIBUTED_COST = """
SELECT
  fh.ck,
  COUNT(*)                      AS execs,
  SUM(a.credits_used_compute)   AS attributed_credits,
  SUM(a.credits_used_compute) * {credit_rate_usd} AS attributed_usd
FROM state.fingerprint_history fh
JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY a
  ON fh.query_id = a.query_id
WHERE fh.d >= DATEADD('day', -{lookback_days}, CURRENT_DATE())
GROUP BY fh.ck
ORDER BY attributed_usd DESC
"""

# §5c — gateway coverage ratio
COVERAGE = """
SELECT
  DATE_TRUNC('DAY', start_time)::DATE                       AS d,
  COUNT_IF(query_text ILIKE '/* abx %')                      AS gateway_routed,
  COUNT(*)                                                  AS total,
  DIV0(COUNT_IF(query_text ILIKE '/* abx %'), COUNT(*))      AS coverage_ratio
FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
WHERE start_time >= DATEADD('day', -{lookback_days}, CURRENT_DATE())
  AND execution_status = 'SUCCESS'
GROUP BY 1
ORDER BY 1
"""

# Incremental ingest — new query_history rows since last checkpoint
INCREMENTAL_HISTORY = """
SELECT
  query_id,
  warehouse_name,
  total_elapsed_time,
  start_time,
  query_text,
  query_text
FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
WHERE start_time > '{checkpoint_ts}'::TIMESTAMP_TZ
  AND execution_status = 'SUCCESS'
  AND query_type NOT IN ('SHOW', 'DESCRIBE')
ORDER BY start_time
"""
