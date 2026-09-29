"""Postgres storage for call records.

One row per call: the columns people filter on (date, practice area, caller,
priority, test flag) plus the full record as JSONB, so nothing the webhook
carries is lost. The old build spread the same data over a table per practice
area; a single table keeps every call queryable in one place, and the JSONB
column absorbs new analysis fields without a migration.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

import asyncpg

from config import DATABASE

logger = logging.getLogger("intake.database")

SCHEMA = """
CREATE TABLE IF NOT EXISTS intake_calls (
    call_id            TEXT PRIMARY KEY,
    room_name          TEXT NOT NULL,
    started_at         TIMESTAMPTZ NOT NULL,
    ended_at           TIMESTAMPTZ,
    duration_ms        INTEGER,
    case_type          TEXT,
    from_number        TEXT,
    caller_first_name  TEXT,
    caller_last_name   TEXT,
    caller_phone       TEXT,
    caller_email       TEXT,
    priority_level     TEXT,
    is_test_call       BOOLEAN NOT NULL DEFAULT FALSE,
    call_successful    BOOLEAN,
    call_summary       TEXT,
    recording_location TEXT,
    record             JSONB NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS intake_calls_started_at_idx ON intake_calls (started_at DESC);
CREATE INDEX IF NOT EXISTS intake_calls_priority_idx ON intake_calls (priority_level, started_at DESC);
"""

UPSERT = """
INSERT INTO intake_calls (
    call_id, room_name, started_at, ended_at, duration_ms, case_type, from_number,
    caller_first_name, caller_last_name, caller_phone, caller_email, priority_level,
    is_test_call, call_successful, call_summary, recording_location, record
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17::jsonb)
ON CONFLICT (call_id) DO UPDATE SET
    ended_at = EXCLUDED.ended_at, duration_ms = EXCLUDED.duration_ms,
    case_type = EXCLUDED.case_type, caller_first_name = EXCLUDED.caller_first_name,
    caller_last_name = EXCLUDED.caller_last_name, caller_phone = EXCLUDED.caller_phone,
    caller_email = EXCLUDED.caller_email, priority_level = EXCLUDED.priority_level,
    is_test_call = EXCLUDED.is_test_call, call_successful = EXCLUDED.call_successful,
    call_summary = EXCLUDED.call_summary, recording_location = EXCLUDED.recording_location,
    record = EXCLUDED.record, updated_at = now()
"""

_schema_ready = False


def _ts(ms: int | None) -> datetime | None:
    return datetime.fromtimestamp(ms / 1000, timezone.utc) if ms else None


async def save(payload: dict[str, Any]) -> None:
    """Upserts one call record (the webhook payload). Raises on failure."""
    global _schema_ready
    call = payload["call"]
    analysis = call["call_analysis"]
    custom = analysis["custom_analysis_data"]
    conn = await asyncpg.connect(DATABASE.url, timeout=10)
    try:
        if not _schema_ready:
            await conn.execute(SCHEMA)
            _schema_ready = True
        await conn.execute(
            UPSERT,
            call["call_id"],
            call["room_name"],
            _ts(call["start_timestamp"]),
            _ts(call["end_timestamp"]),
            call["duration_ms"],
            call["case_type"] or None,
            call["from_number"] or None,
            custom.get("user_fname"),
            custom.get("user_lname"),
            custom.get("user_phone") or custom.get("user_phone_unverified"),
            custom.get("user_email"),
            (analysis.get("priority") or {}).get("priority_level"),
            call["is_test_call"],
            analysis.get("call_successful"),
            analysis.get("call_summary"),
            (call.get("recording") or {}).get("location"),
            json.dumps(payload, ensure_ascii=False, default=str),
        )
    finally:
        await conn.close()
