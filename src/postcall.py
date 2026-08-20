"""Post-call analysis + webhook - the LiveKit equivalent of Retell's
`call_analyzed` event.

Retell ran `post_call_analysis_data` through `post_call_analysis_model`
(gpt-5-mini on these agents) once the call ended, then POSTed the result to
`webhook_url`.  This module does the same, using the field definitions in
`src/schemas.py`, and writes a copy of every call to disk so nothing is lost
if the webhook is down.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import aiohttp
from livekit.agents import AgentSession

from . import settings
from .schemas import FIELDS_BY_CASE_TYPE, field_guide, json_schema_for
from .state import CallState, is_valid_us_number

logger = logging.getLogger("bushbush.postcall")

SYSTEM_PROMPT = """You extract structured intake data from a phone call transcript for a law firm.

Rules:
- Only record what the caller actually said. Never infer, never fill a gap with a plausible guess.
- If a field was not discussed, or you are not confident what was said, return null for it.
- Phone numbers: digits only, exactly 10 US digits. If fewer than 10 digits were said, return null.
- Do not assess the strength of the case, do not give legal opinions.
"""


def build_transcript(session: AgentSession) -> tuple[str, list[dict[str, Any]]]:
    """Return (plain transcript, Retell-style transcript_object)."""
    lines: list[str] = []
    objects: list[dict[str, Any]] = []
    for item in session.history.items:
        role = getattr(item, "role", None)
        if role not in ("user", "assistant"):
            continue
        text = (getattr(item, "text_content", None) or "").strip()
        if not text:
            continue
        speaker = "Agent" if role == "assistant" else "User"
        lines.append(f"{speaker}: {text}")
        objects.append(
            {
                "role": "agent" if role == "assistant" else "user",
                "content": text,
                "created_at": getattr(item, "created_at", None),
            }
        )
    return "\n".join(lines), objects


def _base_payload(
    state: CallState,
    transcript: str,
    transcript_object: list[dict[str, Any]],
) -> dict[str, Any]:
    """Transcript + CallState only — safe to build before LLM analysis."""
    custom: dict[str, Any] = {}
    if state.first_name:
        custom["user_fname"] = state.first_name
    if state.last_name:
        custom["user_lname"] = state.last_name
    if state.phone:
        custom["user_phone"] = state.phone
    if state.other_party_name:
        custom["other_party_name"] = state.other_party_name

    return {
        "event": "call_analyzed",
        "call": {
            **state.to_dict(),
            "call_type": "phone_call",
            "direction": "inbound",
            "transcript": transcript,
            "transcript_object": transcript_object,
            "call_analysis": {
                "custom_analysis_data": custom,
                "call_summary": None,
                "user_sentiment": None,
                "call_successful": None,
                "in_voicemail": None,
            },
        },
    }


def _analysis_schema(case_type: str) -> tuple[dict[str, Any], str]:
    fields = FIELDS_BY_CASE_TYPE.get(case_type, FIELDS_BY_CASE_TYPE["accident"])
    schema = json_schema_for(fields, f"bushbush_{case_type or 'accident'}_analysis")

    # Retell also returned these four alongside custom_analysis_data.
    props = schema["schema"]["properties"]
    props["call_summary"] = {
        "type": ["string", "null"],
        "description": "Two or three sentences summarising the call for the attorney.",
    }
    props["user_sentiment"] = {
        "type": ["string", "null"],
        "description": (
            "The caller's overall sentiment during the call. Must be one of: "
            "Positive, Neutral, Negative, Unknown."
        ),
    }
    props["call_successful"] = {
        "type": ["boolean", "null"],
        "description": (
            "True if the intake gathered at least a name, a 10-digit callback "
            "number, and a description of the matter."
        ),
    }
    props["in_voicemail"] = {
        "type": ["boolean", "null"],
        "description": "True if the call reached a voicemail or answering machine.",
    }
    schema["schema"]["required"] = list(props.keys())
    return schema, field_guide(fields)
def _merge_live_records(custom: dict[str, Any], state: CallState) -> None:
    # Anything captured live is authoritative: it was heard in context, and for
    # the phone number it also passed NANP validation, which a transcript-only
    # pass at the end can get wrong.
    #
    # Optional fields only fill gaps: the post-call model read the whole
    # transcript at once, so where it produced a value that reading wins.
    for name, value in state.optional_fields.items():
        custom.setdefault(name, value)
    if state.first_name:
        custom["user_fname"] = state.first_name
    if state.last_name:
        custom["user_lname"] = state.last_name
    if state.phone:
        custom["user_phone"] = state.phone
    if state.other_party_name:
        custom["other_party_name"] = state.other_party_name
async def analyze(
    session: AgentSession,
    state: CallState,
    *,
    transcript: str | None = None,
    transcript_object: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the extraction model over the transcript."""
    if transcript is None or transcript_object is None:
        transcript, transcript_object = build_transcript(session)

    payload = _base_payload(state, transcript, transcript_object)
    analysis = payload["call"]["call_analysis"]
    custom: dict[str, Any] = dict(analysis["custom_analysis_data"])

    if not transcript.strip():
        logger.info("empty transcript, skipping analysis")
        _merge_live_records(custom, state)
        analysis["custom_analysis_data"] = custom
        return payload

    schema, guide = _analysis_schema(state.case_type)
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.llm.api_key or None)
        completion = await client.chat.completions.create(
            model=settings.llm.post_call_model,
            response_format={"type": "json_schema", "json_schema": schema},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Practice area: {state.case_type or 'unknown'}\n\n"
                        f"Fields to extract:\n{guide}\n\n"
                        f"Transcript:\n{transcript}"
                    ),
                },
            ],
        )
        raw = json.loads(completion.choices[0].message.content or "{}")
        for key, value in raw.items():
            if key in ("call_summary", "user_sentiment", "call_successful", "in_voicemail"):
                analysis[key] = value
            elif key == "user_phone":
                # Retell's field says "must be a valid 10-digit US number", but
                # a transcript-only reading will happily hand back something
                # like 1290909490 (country code plus nine digits). Drop it
                # rather than ship an unreachable number to the firm.
                digits = "".join(ch for ch in str(value or "") if ch.isdigit())
                if len(digits) == 11 and digits.startswith("1"):
                    digits = digits[1:]
                if is_valid_us_number(digits):
                    custom[key] = digits
                elif value:
                    logger.warning("post-call phone %r is not a valid US number", value)
            elif value is not None:
                custom[key] = value
    except Exception:
        logger.exception("post-call analysis failed")
    _merge_live_records(custom, state)
    analysis["custom_analysis_data"] = custom
    return payload
async def deliver(payload: dict[str, Any]) -> None:
    """Save locally, then POST to the configured webhook."""
    records_dir = settings.post_call.records_dir
    try:
        records_dir.mkdir(parents=True, exist_ok=True)
        call_id = payload["call"].get("call_id") or uuid.uuid4().hex
        path = records_dir / f"{int(time.time())}_{call_id}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("call record written to %s", path)
    except Exception:
        logger.exception("could not write call record")

    url = settings.post_call.webhook_url
    if not url:
        logger.debug("POST_CALL_WEBHOOK_URL not set, skipping webhook")
        return

    timeout = aiohttp.ClientTimeout(total=settings.post_call.webhook_timeout_ms / 1000)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.post(url, json=payload) as resp:
                body = await resp.text()
                if resp.status >= 300:
                    logger.warning("webhook returned %s: %s", resp.status, body[:400])
                else:
                    logger.info("webhook delivered (%s)", resp.status)
    except Exception:
        logger.exception("webhook delivery failed")


async def run(
    session: AgentSession,
    state: CallState,
    *,
    transcript: str | None = None,
    transcript_object: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Analyze (with timeout), write once, then webhook."""
    state.ended_at = time.time()

    if transcript is None or transcript_object is None:
        transcript, transcript_object = build_transcript(session)

    timeout_s = settings.post_call.analysis_timeout_ms / 1000
    try:
        payload = await asyncio.wait_for(
            analyze(
                session,
                state,
                transcript=transcript,
                transcript_object=transcript_object,
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "post-call analysis timed out after %.0fms; saving transcript-only record",
            settings.post_call.analysis_timeout_ms,
        )
        payload = _base_payload(state, transcript, transcript_object)

    await deliver(payload)
    return payload
