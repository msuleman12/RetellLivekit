"""After hangup: extract intake fields from the transcript, score and assess
the case, save the record, and deliver it (webhooks, database, intake email)."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

import aiohttp
from livekit.agents import AgentSession
from openai import AsyncOpenAI

from agents.user_data import CallData
from config import DATABASE, EMAIL, OPENAI, POST_CALL, RECORDING
from prompts.common_prompts import POST_CALL_SYSTEM
from services import case_assessment, database, intake_email, priority, priority_pdf, recording, zapier_webhook
from services.analysis_fields import FIELDS_BY_CASE_TYPE, field_guide, json_schema_for
from utils.dates import FIRM_TZ, spoken_date
from utils.phone import is_test_number, normalize_us_phone

logger = logging.getLogger("intake.post_call")

_SUMMARY_FIELDS = {
    "call_summary": {
        "type": ["string", "null"],
        "description": "Two or three sentences summarising the call for the attorney.",
    },
    "user_sentiment": {
        "type": ["string", "null"],
        "description": "The caller's overall sentiment. Must be one of: Positive, Neutral, Negative, Unknown.",
    },
    "call_successful": {
        "type": ["boolean", "null"],
        "description": "True if the intake gathered a name, a 10-digit callback number, and a description of the matter.",
    },
}


def build_transcript(session: AgentSession) -> tuple[str, list[dict[str, Any]]]:
    lines: list[str] = []
    turns: list[dict[str, Any]] = []
    for item in session.history.items:
        if item.type != "message" or item.role not in ("user", "assistant"):
            continue
        text = (item.text_content or "").strip()
        if not text:
            continue
        role = "agent" if item.role == "assistant" else "user"
        lines.append(f"{role.capitalize()}: {text}")
        turns.append({"role": role, "content": text, "created_at": item.created_at})
    return "\n".join(lines), turns


async def analyze(
    case_type: str, transcript: str, call_date: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns (custom_analysis_data, summary fields). call_date resolves relative dates."""
    fields = FIELDS_BY_CASE_TYPE[case_type]
    schema = json_schema_for(fields, f"intake_{case_type}_analysis")
    schema["schema"]["properties"].update(_SUMMARY_FIELDS)
    schema["schema"]["required"] = list(schema["schema"]["properties"])

    async with AsyncOpenAI(api_key=OPENAI.api_key) as client:
        completion = await client.chat.completions.create(
            model=OPENAI.post_call_model,
            response_format={"type": "json_schema", "json_schema": schema},
            messages=[
                {"role": "system", "content": POST_CALL_SYSTEM},
                {
                    "role": "user",
                    "content": f"Practice area: {case_type}\n"
                    f"Call date: {call_date}\n\nFields to extract:\n"
                    f"{field_guide(fields)}\n\nTranscript:\n{transcript}",
                },
            ],
        )
    raw: dict[str, Any] = json.loads(completion.choices[0].message.content)

    summary = {key: raw.pop(key) for key in _SUMMARY_FIELDS}
    custom = {key: value for key, value in raw.items() if value is not None}
    if "user_phone" in custom:
        phone = normalize_us_phone(str(custom["user_phone"]))
        if phone:
            custom["user_phone"] = phone
        else:
            custom["user_phone_unverified"] = custom.pop("user_phone")
    return custom, summary


async def _post(url: str, payload: dict[str, Any]) -> int:
    timeout = aiohttp.ClientTimeout(total=POST_CALL.webhook_timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as http:
        async with http.post(url, json=payload) as resp:
            resp.raise_for_status()
            return resp.status


def _usage(session: AgentSession) -> list[dict[str, Any]]:
    """Tokens, characters and audio seconds per model, for cost tracking."""
    try:
        return [u.model_dump(mode="json") for u in session.usage.model_usage]
    except Exception:
        logger.exception("could not read session usage")
        return []


async def _finish_recording(data: CallData) -> tuple[dict[str, Any] | None, bytes | None]:
    """(recording info for the record, the MP3 when it was already downloaded)."""
    if not RECORDING.enabled:
        return None, None
    if RECORDING.source == "twilio":
        if not data.twilio_call_sid:
            logger.warning("no Twilio Call SID on this call (not a Twilio SIP call?); no recording to fetch")
            return None, None
        try:
            return await recording.fetch_twilio(data.twilio_call_sid)
        except Exception:
            logger.exception("Twilio recording download failed")
            return {"source": "twilio", "call_sid": data.twilio_call_sid, "status": "error"}, None
    if not data.recording_egress_id:
        return None, None
    try:
        return await recording.finish(data.recording_egress_id), None
    except Exception:
        logger.exception("recording finish failed")
        return {"egress_id": data.recording_egress_id, "status": "error"}, None


async def _email(
    data: CallData,
    payload: dict[str, Any],
    custom: dict[str, Any],
    summary: str,
    priority_result: dict[str, Any],
    assessment: dict[str, Any] | None,
    recording_info: dict[str, Any] | None,
    recording_audio: bytes | None,
    is_test_call: bool,
) -> None:
    pdf = None
    try:
        pdf = await asyncio.to_thread(
            priority_pdf.generate,
            case_label=intake_email.case_label(data.case_type),
            full_name=f"{custom.get('user_fname') or ''} {custom.get('user_lname') or ''}".strip(),
            email=custom.get("user_email") or "",
            phone=intake_email.caller_phone(custom) or data.from_number,
            language=intake_email.language_display(),
            priority=priority_result,
            assessment=assessment,
            call_summary=summary,
            call_time=datetime.fromtimestamp(data.started_at, FIRM_TZ),
        )
    except Exception:
        logger.exception("priority PDF generation failed; sending the email without it")

    audio = recording_audio
    max_bytes = EMAIL.max_recording_attachment_mb * 1024 * 1024
    if audio is None and recording_info and recording_info.get("key") and recording_info.get("size_bytes", 0) <= max_bytes:
        try:
            audio = await recording.download(recording_info["key"])
        except Exception:
            logger.exception("recording download failed; the email links it instead")

    status = await intake_email.send(
        data=data,
        custom=custom,
        summary=summary,
        priority=priority_result,
        record=payload,
        is_test_call=is_test_call,
        pdf=pdf,
        recording=recording_info,
        recording_audio=audio,
    )
    logger.info("intake email sent (%s)", status)


async def run(session: AgentSession, data: CallData) -> None:
    transcript, turns = build_transcript(session)
    caller_text = "\n".join(t["content"] for t in turns if t["role"] == "user")
    is_test_call = is_test_number(data.from_number, POST_CALL.test_phone_numbers)

    custom: dict[str, Any] = {}
    summary: dict[str, Any] = {key: None for key in _SUMMARY_FIELDS}

    async def _analyze() -> None:
        nonlocal custom, summary
        if data.case_type and transcript:
            # The transcript is saved even if analysis fails, so no call is ever lost.
            try:
                custom, summary = await analyze(data.case_type, transcript, spoken_date(data.started_at))
            except Exception:
                logger.exception("post-call analysis failed")

    # The recording finishes (egress upload or Twilio processing) while the analysis model runs.
    _, (recording_info, recording_audio) = await asyncio.gather(_analyze(), _finish_recording(data))

    priority_result = priority.calculate(data.case_type, custom, caller_text)
    assessment = case_assessment.assess(data.case_type, custom)
    logger.info("priority %s: %s", priority_result["priority_level"], priority_result["reasoning"])

    payload = {
        "event": "call_analyzed",
        "call": {
            **data.to_dict(),
            "call_type": "phone_call",
            "direction": "inbound",
            "is_test_call": is_test_call,
            "transcript": transcript,
            "transcript_object": turns,
            "recording": recording_info,
            "usage": _usage(session),
            "call_analysis": {
                "custom_analysis_data": custom,
                **summary,
                "priority": priority_result,
                "case_assessment": assessment,
            },
        },
    }

    POST_CALL.records_dir.mkdir(parents=True, exist_ok=True)
    path = POST_CALL.records_dir / f"{int(data.started_at)}_{data.call_id}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("call record written to %s", path)

    # Each delivery is independent: one being down must not cost the others.
    if DATABASE.url:
        try:
            await database.save(payload)
            logger.info("call record saved to the database")
        except Exception:
            logger.exception("database save failed")

    if POST_CALL.webhook_url:
        try:
            status = await _post(POST_CALL.webhook_url, payload)
            logger.info("webhook delivered (%s)", status)
        except Exception:
            logger.exception("webhook delivery failed")

    if POST_CALL.zapier_webhook_url and data.case_type:
        try:
            zap = zapier_webhook.build_payload(
                data,
                custom,
                summary.get("call_summary") or "",
                is_test_call=is_test_call,
                priority_level=priority_result["priority_level"],
                recording_url=(recording_info or {}).get("url") or "",
            )
            status = await _post(POST_CALL.zapier_webhook_url, zap)
            logger.info("Zapier webhook delivered (%s)", status)
        except Exception:
            logger.exception("Zapier webhook delivery failed")

    # Calls that never reached a practice area (wrong numbers, hang-ups at the
    # greeting) are recorded but not emailed, as in the old build.
    if EMAIL.enabled and data.case_type:
        try:
            await _email(
                data, payload, custom, summary.get("call_summary") or "",
                priority_result, assessment, recording_info, recording_audio, is_test_call,
            )
        except Exception:
            logger.exception("intake email failed")
