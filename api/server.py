"""Control API.

    uvicorn api.server:app --host 0.0.0.0 --port 8000

Every route except /health requires the `x-api-key` header.

    GET  /health            liveness
    POST /sessions          room with the agent dispatched + a join token (browser testing)
    GET  /calls             saved call records, newest first
    GET  /calls/{call_id}   one saved record
"""

import hmac
import json
import uuid
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from livekit import api as lkapi
from pydantic import BaseModel

from config import API, LIVEKIT, POST_CALL

if not API.api_key:
    raise RuntimeError("API_KEY is not set. Add it to .env (see .env.example).")

app = FastAPI(title="Bush & Bush intake agent")


async def require_api_key(x_api_key: Annotated[str, Header()] = "") -> None:
    if not hmac.compare_digest(x_api_key.encode(), API.api_key.encode()):
        raise HTTPException(401, "invalid or missing x-api-key header")


Auth = Depends(require_api_key)


class SessionResponse(BaseModel):
    room_name: str
    identity: str
    token: str
    livekit_url: str


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/sessions", response_model=SessionResponse, dependencies=[Auth])
async def create_session() -> SessionResponse:
    room_name = f"intake-{uuid.uuid4().hex[:10]}"
    identity = f"caller-{uuid.uuid4().hex[:6]}"
    token = (
        lkapi.AccessToken(LIVEKIT.api_key, LIVEKIT.api_secret)
        .with_identity(identity)
        .with_grants(lkapi.VideoGrants(room_join=True, room=room_name))
        .with_room_config(
            lkapi.RoomConfiguration(agents=[lkapi.RoomAgentDispatch(agent_name=LIVEKIT.agent_name)])
        )
        .to_jwt()
    )
    return SessionResponse(room_name=room_name, identity=identity, token=token, livekit_url=LIVEKIT.url)


@app.get("/calls", dependencies=[Auth])
async def list_calls(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> dict[str, Any]:
    calls = []
    for path in sorted(POST_CALL.records_dir.glob("*.json"), reverse=True)[:limit]:
        call = json.loads(path.read_text(encoding="utf-8"))["call"]
        calls.append(
            {
                "call_id": call["call_id"],
                "case_type": call["case_type"],
                "from_number": call["from_number"],
                "duration_ms": call["duration_ms"],
                "disconnection_reason": call["disconnection_reason"],
                "call_successful": call["call_analysis"]["call_successful"],
                "call_summary": call["call_analysis"]["call_summary"],
            }
        )
    return {"calls": calls}


@app.get("/calls/{call_id}", dependencies=[Auth])
async def get_call(call_id: str) -> dict[str, Any]:
    if not call_id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "invalid call_id")
    matches = sorted(POST_CALL.records_dir.glob(f"*_{call_id}.json"), reverse=True)
    if not matches:
        raise HTTPException(404, f"no record for call_id {call_id!r}")
    return json.loads(matches[0].read_text(encoding="utf-8"))
