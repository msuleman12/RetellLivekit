"""Control API.

Retell gave you a dashboard and a REST API. This is the equivalent surface for
the LiveKit build - everything you need to drive and inspect the agent without
opening the LiveKit console.

    uvicorn src.api:app --host 0.0.0.0 --port 8000

Every route except `/health` requires the `x-api-key` header to match `API_KEY` in `.env`.

    GET  /health                    liveness
    GET  /config                    the effective Retell-parity settings
    GET  /agents                    the router and the five intake agents
    GET  /agents/{case_type}/prompt the exact prompt an agent runs
    POST /sessions                  room + agent dispatch + a join token
    POST /calls/outbound            dial a PSTN number into the intake flow
    GET  /calls                     saved post-call records, newest first
    GET  /calls/{call_id}           one saved record
"""

from __future__ import annotations

import hmac
import json
import logging
import uuid
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from google.protobuf.duration_pb2 import Duration
from livekit import api as lkapi
from pydantic import BaseModel, Field

from . import prompts, settings
from .agents import AGENTS_BY_CASE_TYPE
from .agents.router import RETELL_AGENT_NAME as ROUTER_AGENT_NAME

logger = logging.getLogger("bushbush.api")

app = FastAPI(title="Bush & Bush intake agent", version="1.0.0")


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------
async def require_api_key(
    x_api_key: Annotated[str, Header()] = "",
) -> None:
    expected = settings.api.api_key
    if not expected:
        raise HTTPException(500, "API_KEY is not configured on the server")
    if not hmac.compare_digest(x_api_key.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(401, "invalid or missing x-api-key header")


Auth = Depends(require_api_key)


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------
class SessionRequest(BaseModel):
    identity: str = Field(default="", description="Participant identity for the caller side.")
    room_name: str = Field(default="", description="Leave empty to generate one.")


class SessionResponse(BaseModel):
    room_name: str
    identity: str
    token: str
    livekit_url: str


class OutboundCallRequest(BaseModel):
    to_number: str = Field(description="E.164 destination, e.g. +16825551234")
    sip_trunk_id: str = Field(description="LiveKit outbound SIP trunk id (ST_...)")
    room_name: str = Field(default="", description="Leave empty to generate one.")
    participant_identity: str = Field(default="")


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config", dependencies=[Auth])
async def config() -> dict[str, Any]:
    min_delay, max_delay = settings.call.endpointing
    return {
        "agent_name": settings.livekit.agent_name,
        "stt": {
            "provider": "deepgram",
            "model": settings.stt.model,
            "language": settings.stt.language,
            "endpointing_ms": settings.stt.endpointing_ms,
            "boosted_keywords": list(settings.call.boosted_keywords),
        },
        "llm": {
            "model": settings.llm.model,
            "temperature": settings.llm.temperature,
            "router_model": settings.llm.router_model,
            "post_call_analysis_model": settings.llm.post_call_model,
        },
        "tts": {
            "provider": "elevenlabs",
            "voice_id": settings.tts.voice_id,
            "model": settings.tts.model,
            "language": settings.tts.language,
            "speed": settings.tts.speed,
            "stability": settings.tts.stability,
            "similarity_boost": settings.tts.similarity_boost,
            "pronunciation_dictionary": bool(settings.tts.pronunciation_dict_id),
        },
        "turn_taking": {
            "latency_profile": settings.call.latency_profile,
            "preemptive_tts": settings.call.is_fast,
            "interruption_sensitivity": settings.call.interruption_sensitivity,
            "interruption_min_duration_s": settings.call.interruption_min_duration,
            "responsiveness": settings.call.responsiveness,
            "endpointing_min_delay_s": min_delay,
            "endpointing_max_delay_s": max_delay,
            "turn_detector": settings.call.semantic_turns,
            "turn_detection_mode": (
                "semantic" if settings.call.semantic_turns else "vad"
            ),
            "noise_cancellation": settings.call.noise_cancellation,
        },
        "call": {
            "max_call_duration_ms": settings.call.max_call_duration_ms,
            "end_call_after_silence_ms": settings.call.end_call_after_silence_ms,
            "reminder_trigger_ms": settings.call.reminder_trigger_ms,
            "reminder_max_count": settings.call.reminder_max_count,
            "begin_message_delay_ms": settings.call.begin_message_delay_ms,
        },
        "post_call": {
            "webhook_configured": bool(settings.post_call.webhook_url),
            "records_dir": str(settings.post_call.records_dir),
        },
        "config_problems": settings.validate(),
    }


@app.get("/agents", dependencies=[Auth])
async def list_agents() -> dict[str, Any]:
    agents = [
        {
            "case_type": "router",
            "name": ROUTER_AGENT_NAME,
            "model": settings.llm.router_model,
            "begin_message": prompts.ROUTER_BEGIN_MESSAGE,
        }
    ]
    for case_type, cls in AGENTS_BY_CASE_TYPE.items():
        agents.append(
            {
                "case_type": case_type,
                "name": cls.retell_agent_name,
                "model": settings.llm.model,
                "begin_message": cls.begin_message,
                "requires_other_party": cls.require_other_party,
            }
        )
    return {"agents": agents}


@app.get("/agents/{case_type}/prompt", dependencies=[Auth])
async def agent_prompt(case_type: str) -> dict[str, Any]:
    if case_type == "router":
        return {"case_type": "router", "instructions": prompts.ROUTER_INSTRUCTIONS}
    cls = AGENTS_BY_CASE_TYPE.get(case_type)
    if cls is None:
        raise HTTPException(404, f"unknown case_type {case_type!r}")
    return {
        "case_type": case_type,
        "name": cls.retell_agent_name,
        "instructions": prompts.compose(cls.source_prompt),
    }


@app.post("/sessions", response_model=SessionResponse, dependencies=[Auth])
async def create_session(req: SessionRequest) -> SessionResponse:
    """Create a room with the intake agent dispatched into it, and return a
    join token. Use it from a browser (or the LiveKit sandbox) to talk to the
    agent without going through the phone line."""
    room_name = req.room_name or f"intake-{uuid.uuid4().hex[:10]}"
    identity = req.identity or f"caller-{uuid.uuid4().hex[:6]}"

    token = (
        lkapi.AccessToken(settings.livekit.api_key, settings.livekit.api_secret)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(lkapi.VideoGrants(room_join=True, room=room_name))
        .with_room_config(
            lkapi.RoomConfiguration(
                agents=[lkapi.RoomAgentDispatch(agent_name=settings.livekit.agent_name)]
            )
        )
        .to_jwt()
    )
    return SessionResponse(
        room_name=room_name,
        identity=identity,
        token=token,
        livekit_url=settings.livekit.url,
    )


@app.post("/calls/outbound", dependencies=[Auth])
async def outbound_call(req: OutboundCallRequest) -> dict[str, Any]:
    """Dial a number and drop it into the same intake flow.

    Retell's separate outbound booking agent is intentionally not part of this
    build - this simply calls someone and runs the inbound intake script.
    """
    room_name = req.room_name or f"outbound-{uuid.uuid4().hex[:10]}"
    identity = req.participant_identity or f"sip-{uuid.uuid4().hex[:6]}"

    async with lkapi.LiveKitAPI(
        url=settings.livekit.url,
        api_key=settings.livekit.api_key,
        api_secret=settings.livekit.api_secret,
    ) as client:
        await client.agent_dispatch.create_dispatch(
            lkapi.CreateAgentDispatchRequest(
                agent_name=settings.livekit.agent_name, room=room_name
            )
        )
        participant = await client.sip.create_sip_participant(
            lkapi.CreateSIPParticipantRequest(
                sip_trunk_id=req.sip_trunk_id,
                sip_call_to=req.to_number,
                room_name=room_name,
                participant_identity=identity,
                wait_until_answered=True,
                # Retell ring_duration_ms / max_call_duration_ms
                ringing_timeout=Duration(seconds=settings.call.ring_duration_ms // 1000),
                max_call_duration=Duration(
                    seconds=settings.call.max_call_duration_ms // 1000
                ),
            )
        )
    return {
        "room_name": room_name,
        "participant_identity": participant.participant_identity,
        "sip_call_id": participant.sip_call_id,
    }


@app.get("/calls", dependencies=[Auth])
async def list_calls(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    directory = settings.post_call.records_dir
    if not directory.exists():
        return {"calls": []}
    files = sorted(directory.glob("*.json"), reverse=True)[:limit]
    calls = []
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.debug("skipping unreadable call record %s", path.name, exc_info=True)
            continue
        record = payload.get("call", {})
        analysis = record.get("call_analysis", {})
        calls.append(
            {
                "call_id": record.get("call_id"),
                "case_type": record.get("case_type"),
                "agent_name": record.get("agent_name"),
                "from_number": record.get("from_number"),
                "duration_ms": record.get("duration_ms"),
                "disconnection_reason": record.get("disconnection_reason"),
                "call_successful": analysis.get("call_successful"),
                "call_summary": analysis.get("call_summary"),
                "file": path.name,
            }
        )
    return {"calls": calls}


@app.get("/calls/{call_id}", dependencies=[Auth])
async def get_call(call_id: str) -> dict[str, Any]:
    if any(ch in call_id for ch in "*?[]/\\"):
        raise HTTPException(400, "invalid call_id")
    directory = settings.post_call.records_dir
    if not directory.exists():
        raise HTTPException(404, f"no record for call_id {call_id!r}")
    candidates = sorted(directory.glob(f"*_{call_id}.json"), reverse=True)
    if not candidates:
        candidates = sorted(directory.glob("*.json"), reverse=True)
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.debug("skipping unreadable call record %s", path.name, exc_info=True)
            continue
        if payload.get("call", {}).get("call_id") == call_id:
            return payload
    raise HTTPException(404, f"no record for call_id {call_id!r}")
