"""Postgres storage for call records, in the old ai-receptionist layout.

user_data holds one row per call (caller, summary, priority, test flag); each
practice area has its own table of intake answers linked to it by user_id;
session_tracking records which agent handled each part of the call; and
error_events records failed transfers and recordings. intake_calls keeps the
full record as JSONB, so nothing the webhook carries is lost.

The practice-area columns are the analysis field names from
services/analysis_fields.py. A field added there gets its column on the next
start; changing an existing field's type needs a manual ALTER.

Everything for one call is written in one transaction keyed on session_id, so
saving the same call again updates its rows instead of duplicating them.
"""

import json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import asyncpg

from config import DATABASE
from services.analysis_fields import FIELDS_BY_CASE_TYPE, AnalysisField
from services.zapier_webhook import AGENT_NAME_BY_CASE_TYPE

logger = logging.getLogger("intake.database")

# Stored on user_data rather than in the practice-area table, as in the old build.
USER_FIELDS = frozenset({
    "user_fname", "user_lname", "user_phone", "user_email", "preferred_contact", "user_address", "user_dob",
})

_COLUMN_TYPES = {"boolean": "BOOLEAN", "number": "NUMERIC", "string": "TEXT", "enum": "TEXT"}
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*\Z")


def _case_fields(fields: tuple[AnalysisField, ...]) -> tuple[AnalysisField, ...]:
    unique: dict[str, AnalysisField] = {}
    for f in fields:
        if f.name in USER_FIELDS or f.name in unique:
            continue
        # Column names are interpolated into SQL, so only plain identifiers are allowed.
        if not _IDENTIFIER.match(f.name) or f.type not in _COLUMN_TYPES:
            raise ValueError(f"analysis field {f.name!r} ({f.type}) cannot be a database column")
        unique[f.name] = f
    return tuple(unique.values())


CASE_FIELDS = {ct: _case_fields(fields) for ct, fields in FIELDS_BY_CASE_TYPE.items()}
AGENT_TABLES = {ct: f"{AGENT_NAME_BY_CASE_TYPE[ct]}_data" for ct in CASE_FIELDS}

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_data (
    id                     BIGSERIAL PRIMARY KEY,
    uuid                   TEXT NOT NULL UNIQUE,
    session_id             TEXT NOT NULL UNIQUE,
    call_id                TEXT,
    first_name             TEXT,
    last_name              TEXT,
    full_name              TEXT,
    email                  TEXT,
    phone_number           TEXT,
    call_number            TEXT,
    current_agent          TEXT,
    call_status            TEXT,
    call_started_at        TIMESTAMPTZ,
    call_ended_at          TIMESTAMPTZ,
    duration_ms            INTEGER,
    total_input_tokens     INTEGER NOT NULL DEFAULT 0,
    total_output_tokens    INTEGER NOT NULL DEFAULT 0,
    -- Not tracked by this build; per-model usage is in intake_calls.record.
    total_cost             NUMERIC(10, 6),
    llm_call_count         INTEGER,
    user_preferred_contact TEXT,
    user_address           TEXT,
    user_dob               TEXT,
    call_summary           TEXT,
    is_test_call           BOOLEAN NOT NULL DEFAULT FALSE,
    language               TEXT NOT NULL DEFAULT 'en',
    priority_level         TEXT,
    matched_keywords       JSONB,
    priority_reasoning     TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_deleted             BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_user_data_call_number ON user_data (call_number);
CREATE INDEX IF NOT EXISTS idx_user_data_call_started_at ON user_data (call_started_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_data_current_agent ON user_data (current_agent);
CREATE INDEX IF NOT EXISTS idx_user_data_priority_level ON user_data (priority_level);
CREATE INDEX IF NOT EXISTS idx_user_data_is_test_call ON user_data (is_test_call);
CREATE INDEX IF NOT EXISTS idx_user_data_phone_number ON user_data (phone_number);

CREATE TABLE IF NOT EXISTS session_tracking (
    id               BIGSERIAL PRIMARY KEY,
    user_id          BIGINT NOT NULL REFERENCES user_data (id) ON DELETE CASCADE,
    session_id       TEXT NOT NULL,
    agent_name       TEXT NOT NULL,
    started_at       TIMESTAMPTZ,
    ended_at         TIMESTAMPTZ,
    transfer_reason  TEXT,
    agent_data_table TEXT
);
CREATE INDEX IF NOT EXISTS idx_session_tracking_session ON session_tracking (session_id);
CREATE INDEX IF NOT EXISTS idx_session_tracking_user_session ON session_tracking (user_id, session_id);
CREATE INDEX IF NOT EXISTS idx_session_tracking_agent ON session_tracking (agent_name);

CREATE TABLE IF NOT EXISTS error_events (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT,
    error_type      TEXT,
    error_message   TEXT,
    recoverable     BOOLEAN,
    room_id         TEXT,
    room_name       TEXT,
    session_id      TEXT,
    twilio_call_sid TEXT,
    extra           JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_error_events_source ON error_events (source);
CREATE INDEX IF NOT EXISTS idx_error_events_session ON error_events (session_id);
CREATE INDEX IF NOT EXISTS idx_error_events_created ON error_events (created_at);
CREATE INDEX IF NOT EXISTS idx_error_events_type ON error_events (error_type);

CREATE TABLE IF NOT EXISTS intake_calls (
    call_id            TEXT PRIMARY KEY,
    session_id         TEXT,
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
CREATE INDEX IF NOT EXISTS intake_calls_session_idx ON intake_calls (session_id);
"""


def _agent_table_ddl(table: str, fields: tuple[AnalysisField, ...]) -> str:
    columns = "".join(f"    {f.name} {_COLUMN_TYPES[f.type]},\n" for f in fields)
    return f"""
CREATE TABLE IF NOT EXISTS {table} (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES user_data (id) ON DELETE CASCADE,
    session_id TEXT NOT NULL UNIQUE,
{columns}    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_{table}_user_session ON {table} (user_id, session_id);
"""


def _upsert(table: str, columns: tuple[str, ...], conflict: str, keep: tuple[str, ...] = ()) -> str:
    """INSERT ... ON CONFLICT DO UPDATE of every column except the conflict key and `keep`."""
    placeholders = ", ".join(f"${i}" for i in range(1, len(columns) + 1))
    updates = "".join(
        f"{c} = EXCLUDED.{c}, " for c in columns if c != conflict and c not in keep
    )
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict}) DO UPDATE SET {updates}updated_at = now()"
    )


USER_COLUMNS = (
    "uuid", "session_id", "call_id", "first_name", "last_name", "full_name", "email", "phone_number",
    "call_number", "current_agent", "call_status", "call_started_at", "call_ended_at", "duration_ms",
    "total_input_tokens", "total_output_tokens", "user_preferred_contact", "user_address", "user_dob",
    "call_summary", "is_test_call", "priority_level", "matched_keywords", "priority_reasoning",
)
# xmax is 0 only on a freshly inserted row, which tells a first save from a re-save.
USER_UPSERT = _upsert("user_data", USER_COLUMNS, "session_id", keep=("uuid",)) + " RETURNING id, (xmax = 0) AS inserted"

AGENT_UPSERTS = {
    ct: _upsert(AGENT_TABLES[ct], ("user_id", "session_id", *(f.name for f in fields)), "session_id")
    for ct, fields in CASE_FIELDS.items()
}

TRACKING_INSERT = """
INSERT INTO session_tracking (user_id, session_id, agent_name, started_at, ended_at, transfer_reason, agent_data_table)
VALUES ($1, $2, $3, $4, $5, $6, $7)
"""

ERROR_INSERT = """
INSERT INTO error_events (source, error_type, error_message, recoverable, room_name, session_id, twilio_call_sid, extra)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
"""

INTAKE_COLUMNS = (
    "call_id", "session_id", "room_name", "started_at", "ended_at", "duration_ms", "case_type", "from_number",
    "caller_first_name", "caller_last_name", "caller_phone", "caller_email", "priority_level",
    "is_test_call", "call_successful", "call_summary", "recording_location", "record",
)
INTAKE_UPSERT = _upsert("intake_calls", INTAKE_COLUMNS, "call_id")

_schema_ready = False


def _ts(ms: int | None) -> datetime | None:
    return datetime.fromtimestamp(ms / 1000, timezone.utc) if ms else None


def _column_value(f: AnalysisField, value: Any) -> Any:
    """The value in the column's type. A mistyped value is left out of its
    column; it is still in intake_calls.record."""
    if value is None:
        return None
    if f.type == "boolean":
        return value if isinstance(value, bool) else None
    if f.type == "number":
        is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
        return Decimal(str(value)) if is_number else None
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


async def _ensure_schema(conn: asyncpg.Connection) -> None:
    global _schema_ready
    if _schema_ready:
        return
    async with conn.transaction():
        # Several workers can start at once; only one creates the tables.
        await conn.execute("SELECT pg_advisory_xact_lock(727401)")
        await conn.execute(SCHEMA)
        for ct, fields in CASE_FIELDS.items():
            await conn.execute(_agent_table_ddl(AGENT_TABLES[ct], fields))
        existing = {
            (r["table_name"], r["column_name"])
            for r in await conn.fetch(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = ANY($1::text[])",
                list(AGENT_TABLES.values()),
            )
        }
        for ct, fields in CASE_FIELDS.items():
            table = AGENT_TABLES[ct]
            for f in fields:
                if (table, f.name) not in existing:
                    await conn.execute(f"ALTER TABLE {table} ADD COLUMN {f.name} {_COLUMN_TYPES[f.type]}")
                    logger.info("added column %s.%s", table, f.name)
    _schema_ready = True


def _tracking_rows(call: dict[str, Any], user_id: int) -> list[tuple[Any, ...]]:
    """One row per agent that handled the call: the router, then each practice
    area it was handed to."""
    segments: list[tuple[str | None, datetime | None]] = [(None, _ts(call["start_timestamp"]))]
    for handoff in call.get("handoffs") or []:
        segments.append((handoff.get("to"), _ts(handoff.get("at"))))
    ended = _ts(call["end_timestamp"])
    human_transfer_reason = (call.get("transfer_reason") or None) if call.get("transfer_requested") else None

    rows = []
    for i, (case_type, started) in enumerate(segments):
        last = i == len(segments) - 1
        rows.append((
            user_id,
            call["session_id"],
            AGENT_NAME_BY_CASE_TYPE.get(case_type, case_type) if case_type else "router",
            started,
            ended if last else segments[i + 1][1],
            human_transfer_reason if last else None,
            AGENT_TABLES.get(case_type),
        ))
    return rows


def _error_rows(call: dict[str, Any]) -> list[tuple[Any, ...]]:
    rows = []
    context = (call["room_name"], call["session_id"], call.get("twilio_call_sid") or None)
    if call.get("transfer_attempted") and not call.get("transfer_succeeded"):
        code = call.get("transfer_sip_code")
        outcome = call.get("transfer_outcome") or "failed"
        rows.append((
            "livekit_sip",
            f"SIP_{code}" if code else f"transfer_{outcome}",
            f"Live transfer did not connect ({outcome}); the call continued with the AI.",
            True,
            *context,
            json.dumps({
                "sip_code": code,
                "outcome": outcome,
                "target": call.get("transfer_target"),
                "reason": call.get("transfer_reason"),
            }),
        ))
    recording = call.get("recording") or {}
    if recording.get("status") == "error":
        rows.append((
            "recording",
            "recording_failed",
            f"Call recording could not be retrieved from {recording.get('source') or 'egress'}.",
            False,
            *context,
            json.dumps(recording, default=str),
        ))
    return rows


def _llm_tokens(usage: list[dict[str, Any]] | None) -> tuple[int, int]:
    llm = [u for u in usage or [] if u.get("type") == "llm_usage"]
    return (
        sum(int(u.get("input_tokens") or 0) for u in llm),
        sum(int(u.get("output_tokens") or 0) for u in llm),
    )


async def save(payload: dict[str, Any]) -> None:
    """Saves one call record (the webhook payload) across the tables. Raises on failure."""
    call = payload["call"]
    analysis = call["call_analysis"]
    custom = analysis["custom_analysis_data"]
    priority = analysis.get("priority") or {}
    case_type = call["case_type"] or None
    session_id = call["session_id"]

    first = custom.get("user_fname")
    last = custom.get("user_lname")
    phone = custom.get("user_phone") or custom.get("user_phone_unverified")
    input_tokens, output_tokens = _llm_tokens(call.get("usage"))
    user_row = {
        "uuid": str(uuid4()),
        "session_id": session_id,
        "call_id": call["call_id"],
        "first_name": first,
        "last_name": last,
        "full_name": f"{first or ''} {last or ''}".strip() or None,
        "email": custom.get("user_email"),
        "phone_number": phone,
        "call_number": call["from_number"] or None,
        "current_agent": AGENT_NAME_BY_CASE_TYPE.get(case_type) if case_type else None,
        "call_status": "transferred" if call.get("transfer_succeeded") else "completed",
        "call_started_at": _ts(call["start_timestamp"]),
        "call_ended_at": _ts(call["end_timestamp"]),
        "duration_ms": call["duration_ms"],
        "total_input_tokens": input_tokens,
        "total_output_tokens": output_tokens,
        "user_preferred_contact": custom.get("preferred_contact"),
        "user_address": custom.get("user_address"),
        "user_dob": custom.get("user_dob"),
        "call_summary": analysis.get("call_summary"),
        "is_test_call": call["is_test_call"],
        "priority_level": priority.get("priority_level"),
        "matched_keywords": json.dumps(priority.get("matched_keywords") or [], ensure_ascii=False),
        "priority_reasoning": priority.get("reasoning"),
    }
    intake_row = {
        "call_id": call["call_id"],
        "session_id": session_id,
        "room_name": call["room_name"],
        "started_at": _ts(call["start_timestamp"]),
        "ended_at": _ts(call["end_timestamp"]),
        "duration_ms": call["duration_ms"],
        "case_type": case_type,
        "from_number": call["from_number"] or None,
        "caller_first_name": first,
        "caller_last_name": last,
        "caller_phone": phone,
        "caller_email": custom.get("user_email"),
        "priority_level": priority.get("priority_level"),
        "is_test_call": call["is_test_call"],
        "call_successful": analysis.get("call_successful"),
        "call_summary": analysis.get("call_summary"),
        "recording_location": (call.get("recording") or {}).get("location"),
        "record": json.dumps(payload, ensure_ascii=False, default=str),
    }

    conn = await asyncpg.connect(DATABASE.url, timeout=10)
    try:
        await _ensure_schema(conn)
        async with conn.transaction():
            user = await conn.fetchrow(USER_UPSERT, *(user_row[c] for c in USER_COLUMNS))
            user_id = user["id"]

            if case_type in CASE_FIELDS:
                fields = CASE_FIELDS[case_type]
                await conn.execute(
                    AGENT_UPSERTS[case_type],
                    user_id,
                    session_id,
                    *(_column_value(f, custom.get(f.name)) for f in fields),
                )

            await conn.execute("DELETE FROM session_tracking WHERE session_id = $1", session_id)
            await conn.executemany(TRACKING_INSERT, _tracking_rows(call, user_id))

            # Only on the first save, so a re-save does not record the same failure twice.
            errors = _error_rows(call)
            if user["inserted"] and errors:
                await conn.executemany(ERROR_INSERT, errors)

            await conn.execute(INTAKE_UPSERT, *(intake_row[c] for c in INTAKE_COLUMNS))
    finally:
        await conn.close()
