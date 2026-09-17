"""Bush & Bush Law Group - inbound phone intake agent.

    python main.py console   # talk to it in the terminal
    python main.py dev       # connect to LiveKit, hot reload
    python main.py start     # production
"""

import asyncio
import logging

from livekit.agents import (
    AgentServer,
    AgentSession,
    CloseEvent,
    ConversationItemAddedEvent,
    JobContext,
    UserStateChangedEvent,
    cli,
    room_io,
)
from livekit.plugins import noise_cancellation

from agents.router.agent import RouterAgent
from agents.user_data import CallData
from config import CALL, LIVEKIT
from prompts.common_prompts import (
    MAX_DURATION_GOODBYE,
    SILENCE_GOODBYE,
    SILENCE_REMINDER_INSTRUCTIONS,
)
from services import post_call
from services.voice_pipeline import build_llm, build_stt, build_tts, build_turn_handling
from utils.phone import mask_phone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
logger = logging.getLogger("intake")


def _ms(seconds: float | None) -> str:
    return "-" if seconds is None else f"{seconds * 1000:.0f}ms"


# Post-call analysis runs during job shutdown; the 10s default can cut it off.
server = AgentServer(shutdown_process_timeout=60.0, port=LIVEKIT.http_port)


@server.rtc_session(agent_name=LIVEKIT.agent_name)
async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    # The caller's SIP details are read after the session starts. Waiting for
    # them first delayed the greeting by seconds of silence on the line.
    data = CallData(call_id=ctx.room.name, room_name=ctx.room.name)
    ctx.log_context_fields = {"room": ctx.room.name}

    session = AgentSession[CallData](
        userdata=data,
        stt=build_stt(),
        llm=build_llm(),
        tts=build_tts(),
        turn_handling=build_turn_handling(),
        user_away_timeout=CALL.user_away_timeout_s,
    )

    # How long the caller waited for Claire to start speaking, per turn.
    latencies: list[float] = []

    @session.on("conversation_item_added")
    def on_item_added(ev: ConversationItemAddedEvent) -> None:
        metrics = ev.item.metrics if ev.item.type == "message" and ev.item.role == "assistant" else {}
        if (e2e := metrics.get("e2e_latency")) is None:
            return
        latencies.append(e2e)
        logger.info(
            "reply latency %s | end of turn %s + llm %s + tts %s",
            _ms(e2e),
            _ms(metrics.get("end_of_turn_delay")),
            _ms(metrics.get("llm_node_ttft")),
            _ms(metrics.get("tts_node_ttfb")),
        )

    async def end_call(goodbye: str, reason: str) -> None:
        data.end(reason)
        await session.say(goodbye, allow_interruptions=False)
        session.shutdown()

    async def check_in_on_silent_caller() -> None:
        for _ in range(CALL.silence_reminders):
            await session.generate_reply(instructions=SILENCE_REMINDER_INSTRUCTIONS, tool_choice="none")
            await asyncio.sleep(CALL.user_away_timeout_s)
        await end_call(SILENCE_GOODBYE, "silence_timeout")

    silence_task: asyncio.Task | None = None

    @session.on("user_state_changed")
    def on_user_state_changed(ev: UserStateChangedEvent) -> None:
        nonlocal silence_task
        if silence_task:
            silence_task.cancel()
            silence_task = None
        if ev.new_state == "away":
            silence_task = asyncio.create_task(check_in_on_silent_caller())

    @session.on("close")
    def on_close(ev: CloseEvent) -> None:
        data.end(ev.reason.value)

    async def enforce_max_duration() -> None:
        await asyncio.sleep(CALL.max_duration_s)
        logger.info("max call duration reached")
        await end_call(MAX_DURATION_GOODBYE, "max_duration_reached")

    max_duration_task = asyncio.create_task(enforce_max_duration())

    async def on_shutdown() -> None:
        max_duration_task.cancel()
        if silence_task:
            silence_task.cancel()
        data.end("user_hangup")
        if latencies:
            logger.info(
                "call latency: average %s, slowest %s, over %d replies",
                _ms(sum(latencies) / len(latencies)),
                _ms(max(latencies)),
                len(latencies),
            )
        await post_call.run(session, data)

    ctx.add_shutdown_callback(on_shutdown)

    await session.start(
        agent=RouterAgent(),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=noise_cancellation.BVCTelephony(),
            ),
            # Ending the session always hangs up the phone line.
            delete_room_on_close=True,
        ),
    )

    participant = session.room_io.linked_participant or await ctx.wait_for_participant()
    attrs = participant.attributes
    data.call_id = attrs.get("sip.callID") or ctx.room.name
    data.from_number = attrs.get("sip.phoneNumber", "")
    data.to_number = attrs.get("sip.trunkPhoneNumber", "")
    ctx.log_context_fields = {"room": ctx.room.name, "call_id": data.call_id}
    logger.info("call from %s to %s", mask_phone(data.from_number), mask_phone(data.to_number))


if __name__ == "__main__":
    cli.run_app(server)
