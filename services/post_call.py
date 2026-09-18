"""After hangup: extract intake fields from the transcript, save the record,
and deliver it to the configured webhooks."""

import json
import logging
from typing import Any

import aiohttp
from livekit.agents import AgentSession
from openai import AsyncOpenAI

from agents.user_data import CallData
from config import OPENAI, POST_CALL
from prompts.common_prompts import POST_CALL_SYSTEM
from services import zapier_webhook
from services.analysis_fields import FIELDS_BY_CASE_TYPE, field_guide, json_schema_for
from utils.dates import spoken_date
from utils.phone import normalize_us_phone

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


async def run(session: AgentSession, data: CallData) -> None:
    transcript, turns = build_transcript(session)

    custom: dict[str, Any] = {}
    summary: dict[str, Any] = {key: None for key in _SUMMARY_FIELDS}
    if data.case_type and transcript:
        # The transcript is saved even if analysis fails, so no call is ever lost.
        try:
            custom, summary = await analyze(data.case_type, transcript, spoken_date(data.started_at))
        except Exception:
            logger.exception("post-call analysis failed")

    payload = {
        "event": "call_analyzed",
        "call": {
            **data.to_dict(),
            "call_type": "phone_call",
            "direction": "inbound",
            "transcript": transcript,
            "transcript_object": turns,
            "call_analysis": {"custom_analysis_data": custom, **summary},
        },
    }

    POST_CALL.records_dir.mkdir(parents=True, exist_ok=True)
    path = POST_CALL.records_dir / f"{int(data.started_at)}_{data.call_id}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("call record written to %s", path)

    # Each delivery is independent: one endpoint being down must not cost the other.
    if POST_CALL.webhook_url:
        try:
            status = await _post(POST_CALL.webhook_url, payload)
            logger.info("webhook delivered (%s)", status)
        except Exception:
            logger.exception("webhook delivery failed")

    if POST_CALL.zapier_webhook_url and data.case_type:
        try:
            zap = zapier_webhook.build_payload(data, custom, summary.get("call_summary") or "")
            status = await _post(POST_CALL.zapier_webhook_url, zap)
            logger.info("Zapier webhook delivered (%s)", status)
        except Exception:
            logger.exception("Zapier webhook delivery failed")
