"""The LiveKit worker.

Run it with:

    python -m src.worker dev      # local development, hot reload
    python -m src.worker start    # production

A SIP dispatch rule (see scripts/setup_sip.py) points the firm's phone number
at `AGENT_NAME`, so an inbound PSTN call creates a room, this worker picks up
the job, and `RouterAgent` answers - the same entry point Retell's
`+1 682 564 1506 -> Router` binding gave you.
"""

from __future__ import annotations

import logging
import os

from livekit.agents import (
    NOT_GIVEN,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RoomInputOptions,
    cli,
)

from . import models, postcall, settings
from .agents import RouterAgent
from .lifecycle import CallLifecycle
from .state import CallState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

# Console mode repaints a status bar over the terminal, so log lines scroll out
# of reach. Set LOG_FILE in .env (or the environment) to keep a plain copy:
#
#     LOG_FILE=run.log
#     python -m src.worker console
#     Select-String "turn timing" run.log        # PowerShell
#
# Piping the worker itself is not an option - console mode needs the terminal
# for the microphone UI.
_log_file = os.getenv("LOG_FILE", "").strip()
if _log_file:
    _handler = logging.FileHandler(_log_file, mode="a", encoding="utf-8")
    _handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    logging.getLogger().addHandler(_handler)

logger = logging.getLogger("bushbush.worker")


def prewarm(proc: JobProcess) -> None:
    """Load the VAD once per process instead of once per call."""
    proc.userdata["vad"] = models.build_vad()


server = AgentServer(setup_fnc=prewarm)


@server.rtc_session(agent_name=settings.livekit.agent_name)
async def entrypoint(ctx: JobContext) -> None:
    problems = settings.validate()
    for problem in problems:
        logger.warning("config: %s", problem)

    await ctx.connect()

    state = CallState(room_name=ctx.room.name, call_id=ctx.room.name)

    participant = await ctx.wait_for_participant()
    attrs = dict(participant.attributes or {})
    state.from_number = attrs.get("sip.phoneNumber", "")
    state.to_number = attrs.get("sip.trunkPhoneNumber", "")
    if sip_call_id := attrs.get("sip.callID"):
        state.call_id = sip_call_id
    ctx.log_context_fields = {
        "room": ctx.room.name,
        "from": state.from_number,
    }
    logger.info(
        "call from %s to %s (latency_profile=%s)",
        state.from_number or "web",
        state.to_number or "-",
        settings.call.latency_profile,
    )

    session: AgentSession[CallState] = AgentSession[CallState](
        userdata=state,
        stt=models.build_stt(),
        llm=models.build_llm(),
        tts=models.build_tts(),
        vad=ctx.proc.userdata.get("vad") or models.build_vad(),
        turn_handling=models.build_turn_handling(),
        # Retell max_tool_steps equivalent: enough for record_* + a reply.
        max_tool_steps=5,
        # Fast: shorten echo-warmup so interruptions aren't blocked for 3s.
        aec_warmup_duration=0.5 if settings.call.is_fast else NOT_GIVEN,
    )

    lifecycle = CallLifecycle(session, state)

    async def _on_shutdown() -> None:
        await lifecycle.aclose()
        if not state.disconnect_reason:
            state.disconnect_reason = "user_hangup"

        # Capture transcript before closing the session (history may clear).
        transcript, transcript_object = postcall.build_transcript(session)

        # Close TTS/STT websockets cleanly before post-call work.
        try:
            await session.aclose()
        except Exception:
            logger.debug("session close during shutdown", exc_info=True)

        await postcall.run(
            session,
            state,
            transcript=transcript,
            transcript_object=transcript_object,
        )

    ctx.add_shutdown_callback(_on_shutdown)

    room_input_options = RoomInputOptions(
        noise_cancellation=models.build_noise_cancellation(),
    )

    await session.start(
        agent=RouterAgent(),
        room=ctx.room,
        room_input_options=room_input_options,
    )
    lifecycle.start()


if __name__ == "__main__":
    cli.run_app(server)
