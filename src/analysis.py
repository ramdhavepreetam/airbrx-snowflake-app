"""
Airbrx analysis — Snowflake Stored Procedure.
Called by the scheduled Task every 2 hours (ACCOUNT_USAGE has ~45-min latency).

Handler: analysis.main  (referenced in setup.sql CREATE PROCEDURE)

Modes:
  ingest    — pull new QUERY_HISTORY rows → fingerprint_history
  recompute — rebuild waterfall_daily, coverage_daily, rule_effectiveness
  sync      — push aggregates to api.airbrx.ai, pull rules
  all       — all three in order (default)
"""
import logging
import os
from datetime import datetime, timezone, timedelta

from fingerprint import fingerprint
from tags import parse_tag

logger = logging.getLogger(__name__)

LOOKBACK_DAYS    = 30
INSERT_BATCH     = 500
CREDIT_RATE_USD  = float(os.environ.get("AIRBRX_CREDIT_RATE_USD", "3.00"))
AIRBRX_API_URL   = os.environ.get("AIRBRX_API_URL", "https://api.airbrx.ai")


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------
def _get_checkpoint(session) -> datetime:
    try:
        row = session.sql(
            "SELECT MAX(start_time) FROM state.fingerprint_history"
        ).collect()[0]
        val = row[0]
        if val:
            return val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val
    except Exception:
        pass
    return datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)


def run_ingest(session) -> None:
    checkpoint = _get_checkpoint(session)
    cp_str     = checkpoint.strftime("%Y-%m-%d %H:%M:%S")
    logger.info("Ingest checkpoint: %s", cp_str)

    rows = session.sql(f"""
        SELECT query_id, warehouse_name, total_elapsed_time,
               start_time, query_text
        FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
        WHERE start_time > '{cp_str}'::TIMESTAMP_TZ
          AND execution_status = 'SUCCESS'
          AND query_type NOT IN ('SHOW', 'DESCRIBE')
        ORDER BY start_time
    """).collect()

    logger.info("Fetched %d new rows", len(rows))
    if not rows:
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    records = []
    for r in rows:
        # The gateway prepends /* abx ... */ to query_text. Parse from there.
        # query_tag is a session-level string (ALTER SESSION SET QUERY_TAG) — not used.
        tag_src = r["QUERY_TEXT"] or ""
        tag     = parse_tag(tag_src)
        ck      = fingerprint(r["QUERY_TEXT"] or "")
        st      = r["START_TIME"]
        st_str  = st.strftime("%Y-%m-%d %H:%M:%S") if st else None
        d_str   = st.strftime("%Y-%m-%d") if st else None
        wh      = (r["WAREHOUSE_NAME"] or "").replace("'", "''")
        ms      = r["TOTAL_ELAPSED_TIME"]
        route   = (tag.get("route") if tag else None) or ""
        rule    = (tag.get("rule")  if tag else None) or ""
        qid     = (r["QUERY_ID"] or "").replace("'", "''")
        records.append(
            f"('{ck}','{qid}','{d_str}'::DATE,'{st_str}'::TIMESTAMP_TZ,"
            f"{'NULL' if not wh else repr(wh)},"
            f"{'NULL' if ms is None else int(ms)},"
            f"{'NULL' if not route else repr(route)},"
            f"{'NULL' if not rule  else repr(rule)},"
            f"'{now}'::TIMESTAMP_TZ)"
        )

    inserted = 0
    for i in range(0, len(records), INSERT_BATCH):
        batch  = records[i : i + INSERT_BATCH]
        values = ",\n".join(batch)
        session.sql(f"""
            INSERT INTO state.fingerprint_history
              (ck, query_id, d, start_time, warehouse_name,
               total_elapsed_time, route, rule_id, ingested_at)
            VALUES {values}
        """).collect()
        inserted += len(batch)

    logger.info("Ingest complete — %d rows written", inserted)


# ---------------------------------------------------------------------------
# Recompute
# ---------------------------------------------------------------------------
def run_recompute(session) -> None:
    logger.info("Recompute started")
    _recompute_waterfall(session)
    _recompute_coverage(session)
    _recompute_rule_effectiveness(session)
    logger.info("Recompute complete")


def _recompute_waterfall(session) -> None:
    session.sql("TRUNCATE TABLE IF EXISTS state.waterfall_daily").collect()
    session.sql(f"""
        INSERT INTO state.waterfall_daily (d, warehouse_name, credits, usd, layer, updated_at)
        SELECT
          DATE_TRUNC('DAY', a.start_time)::DATE           AS d,
          h.warehouse_name,
          SUM(a.credits_used_compute)                     AS credits,
          SUM(a.credits_used_compute) * {CREDIT_RATE_USD} AS usd,
          'raw'                                           AS layer,
          CURRENT_TIMESTAMP()                             AS updated_at
        FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY a
        JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY h USING (query_id)
        WHERE a.start_time >= DATEADD('day', -{LOOKBACK_DAYS}, CURRENT_DATE())
          AND h.warehouse_name IS NOT NULL
        GROUP BY 1, 2
    """).collect()
    logger.info("waterfall_daily updated")


def _recompute_coverage(session) -> None:
    session.sql("TRUNCATE TABLE IF EXISTS state.coverage_daily").collect()
    session.sql(f"""
        INSERT INTO state.coverage_daily (d, gateway_routed, total, coverage_ratio, updated_at)
        SELECT
          DATE_TRUNC('DAY', start_time)::DATE                        AS d,
          COUNT_IF(query_text ILIKE '/* abx %')                      AS gateway_routed,
          COUNT(*)                                                   AS total,
          DIV0(COUNT_IF(query_text ILIKE '/* abx %'), COUNT(*))       AS coverage_ratio,
          CURRENT_TIMESTAMP()                                        AS updated_at
        FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
        WHERE start_time >= DATEADD('day', -{LOOKBACK_DAYS}, CURRENT_DATE())
          AND execution_status = 'SUCCESS'
        GROUP BY 1
        ORDER BY 1
    """).collect()
    logger.info("coverage_daily updated")


def _recompute_rule_effectiveness(session) -> None:
    session.sql("TRUNCATE TABLE IF EXISTS state.rule_effectiveness").collect()
    session.sql(f"""
        INSERT INTO state.rule_effectiveness
          (ck, rule_id, window, baseline_rate, observed, avoided, realized_usd, updated_at)
        WITH attributed AS (
          SELECT fh.ck,
                 SUM(a.credits_used_compute) * {CREDIT_RATE_USD} AS attributed_usd,
                 COUNT(*)                                        AS execs
          FROM state.fingerprint_history fh
          JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY a
            ON fh.query_id = a.query_id
          WHERE fh.d >= DATEADD('day', -{LOOKBACK_DAYS}, CURRENT_DATE())
          GROUP BY fh.ck
        ),
        baseline AS (
          SELECT ck, rule_id, COUNT(*) / 30.0 AS baseline_rate
          FROM state.fingerprint_history
          WHERE d BETWEEN DATEADD('day', -60, CURRENT_DATE())
                      AND DATEADD('day', -31, CURRENT_DATE())
          GROUP BY ck, rule_id
        ),
        current_window AS (
          SELECT ck, COUNT(*) AS observed
          FROM state.fingerprint_history
          WHERE d >= DATEADD('day', -30, CURRENT_DATE())
          GROUP BY ck
        )
        SELECT
          b.ck,
          b.rule_id,
          TO_VARCHAR(DATEADD('day',-30,CURRENT_DATE()),'YYYY-MM-DD') || '/' ||
            TO_VARCHAR(CURRENT_DATE(),'YYYY-MM-DD')                       AS window,
          b.baseline_rate,
          c.observed,
          GREATEST(0, CAST(b.baseline_rate * 30 - c.observed AS BIGINT)) AS avoided,
          GREATEST(0.0, b.baseline_rate * 30 - c.observed)
            * COALESCE(a.attributed_usd / NULLIF(a.execs, 0), 0.0)       AS realized_usd,
          CURRENT_TIMESTAMP()                                             AS updated_at
        FROM baseline b
        JOIN current_window c USING (ck)
        LEFT JOIN attributed a USING (ck)
    """).collect()
    logger.info("rule_effectiveness updated")


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------
def run_sync(session) -> None:
    from airbrx_client import AirbrxClient
    client = AirbrxClient(session, api_url=AIRBRX_API_URL)
    client.sync(session)


# ---------------------------------------------------------------------------
# Entrypoint (called by the stored procedure handler)
# ---------------------------------------------------------------------------
def main(session, mode: str = "all") -> str:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    mode = (mode or "all").lower()
    try:
        if mode in ("ingest",    "all"): run_ingest(session)
        if mode in ("recompute", "all"): run_recompute(session)
        if mode in ("sync",      "all"): run_sync(session)
        return f"OK: mode={mode}"
    except Exception as exc:
        logger.exception("Analysis failed: %s", exc)
        return f"ERROR: {exc}"
