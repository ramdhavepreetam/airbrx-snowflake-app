"""
Airbrx API client — Snowflake edition.
Reads the API key from a Snowflake SECRET object via SYSTEM$GET_SECRET_VALUE.
Only aggregates and rule IDs cross the boundary — no query text, no raw rows.
"""
import logging
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)
_DEFAULT_TIMEOUT = 15


class AirbrxClient:
    def __init__(self, session, api_url: str = "https://api.airbrx.ai"):
        self._session  = session
        self._base_url = api_url.rstrip("/")
        self._api_key: str | None = None

    def _get_api_key(self) -> str:
        if self._api_key is None:
            rows = self._session.sql(
                "SELECT SYSTEM$GET_SECRET_VALUE('airbrx_api_key')"
            ).collect()
            self._api_key = rows[0][0]
        return self._api_key

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_api_key()}",
            "Content-Type":  "application/json",
            "X-Client":      "snowflake-app-v1",
        }

    def pull_rules(self) -> list[dict]:
        resp = requests.get(
            f"{self._base_url}/v1/rules",
            headers=self._headers(),
            timeout=_DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("rules", [])

    def push_effectiveness(self, rows: list[dict[str, Any]]) -> None:
        payload = {
            "reported_at":   datetime.now(timezone.utc).isoformat(),
            "effectiveness": rows,
        }
        resp = requests.post(
            f"{self._base_url}/v1/effectiveness",
            json=payload,
            headers=self._headers(),
            timeout=_DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()

    def sync(self, session, state_schema: str = "state") -> None:
        ts     = datetime.now(timezone.utc)
        status = "ok"
        payload_bytes = 0
        try:
            rules = self.pull_rules()
            _upsert_rules(session, rules, state_schema)

            eff_rows = _read_effectiveness(session, state_schema)
            self.push_effectiveness(eff_rows)
            payload_bytes = len(str(eff_rows).encode())
            logger.info("Sync complete — pushed %d effectiveness rows", len(eff_rows))
        except Exception as exc:
            status = f"error: {exc}"
            logger.exception("Airbrx sync failed")
            raise
        finally:
            _log_sync(session, ts, status, payload_bytes, state_schema)


def _upsert_rules(session, rules: list[dict], schema: str) -> None:
    if not rules:
        return
    import json
    values = ", ".join(
        f"('{r['rule_id']}', '{r.get('rule_type','')}', "
        f"'{str(r.get('description','')).replace(chr(39), chr(39)*2)}', "
        f"PARSE_JSON('{json.dumps(r).replace(chr(39), chr(39)*2)}'), "
        f"CURRENT_TIMESTAMP())"
        for r in rules
    )
    session.sql(f"""
        MERGE INTO {schema}.rules AS target
        USING (SELECT * FROM VALUES {values}
               AS t(rule_id, rule_type, description, config_json, pulled_at))
          AS source ON target.rule_id = source.rule_id
        WHEN MATCHED     THEN UPDATE SET
          target.rule_type    = source.rule_type,
          target.description  = source.description,
          target.config_json  = source.config_json,
          target.pulled_at    = source.pulled_at
        WHEN NOT MATCHED THEN INSERT
          (rule_id, rule_type, description, config_json, pulled_at)
          VALUES (source.rule_id, source.rule_type, source.description,
                  source.config_json, source.pulled_at)
    """).collect()


def _read_effectiveness(session, schema: str) -> list[dict]:
    rows = session.sql(f"""
        SELECT rule_id, ck, window, avoided, realized_usd
        FROM {schema}.rule_effectiveness
        WHERE window = (SELECT MAX(window) FROM {schema}.rule_effectiveness)
    """).collect()
    return [r.as_dict() for r in rows]


def _log_sync(session, ts, status: str, payload_bytes: int, schema: str) -> None:
    d_str  = ts.date().isoformat()
    ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
    safe   = status.replace("'", "''")
    session.sql(f"""
        INSERT INTO {schema}.sync_log (d, ts, direction, payload_bytes, status)
        VALUES ('{d_str}'::DATE, '{ts_str}'::TIMESTAMP_TZ,
                'push_pull', {payload_bytes}, '{safe}')
    """).collect()
