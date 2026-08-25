"""Shared behaviour for the five Bush & Bush intake agents.

Two things here are deliberate departures from the first port of this project,
and both exist to make LiveKit behave the way Retell actually behaved.

**The agent decides when to hang up, not a counter.**  Retell handed the model
one tool, `end_call`, with a long description spelling out what had to be true
first, and trusted the model to choose the moment.  The earlier LiveKit version
removed the tool and hung up the instant a `missing_must_haves()` list came back
empty — which meant the call could be cut off in the middle of the caller's next
sentence.  The tool is back, with Retell's description copied verbatim, and the
"FORBIDDEN" section of Retell's prompt is enforced as a *gate*: if the model
calls `end_call` too early, the tool refuses, tells the model what is still
missing, and the conversation carries on.  Nothing else in this file can end a
call.

**Fields are captured by a model, not by regular expressions.**  See
`src/extract.py`.  It runs in the background after every caller turn so it costs
Claire no latency, and `capture.py` still runs inline as an instant fast-path.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterable

from livekit import api, rtc
from livekit.agents import (
    Agent,
    AgentSession,
    ModelSettings,
    NOT_GIVEN,
    RunContext,
    StopResponse,
    function_tool,
    get_job_context,
    llm,
)

from .. import prompts, settings
from ..capture import auto_capture_from_utterance, default_farewell, utterance_text
from ..extract import LiveExtractor
from ..models import uses_elevenlabs_dictionary
from ..pronunciation import apply_pronunciation
from ..state import CallState
from ..turntaking import FragmentBuffer

logger = logging.getLogger("bushbush.agent")


def last_assistant_text(turn_ctx: llm.ChatContext) -> str:
    """Most recent assistant utterance in the turn context."""
    for item in reversed(list(turn_ctx.items)):
        if getattr(item, "role", None) == "assistant":
            return utterance_text(item)
    return ""


def flatten_transcript(turn_ctx: llm.ChatContext, latest_user_text: str = "") -> str:
    """Flatten chat history for the extractor / classifier.

    The turn that just finished is not necessarily in `turn_ctx` yet, so
    `latest_user_text` is appended when it is missing.
    """
    lines: list[str] = []
    for item in turn_ctx.items:
        role = getattr(item, "role", None)
        if role not in ("user", "assistant"):
            continue
        text = (getattr(item, "text_content", None) or "").strip()
        if text:
            lines.append(f"{'Agent' if role == 'assistant' else 'User'}: {text}")
    latest = (latest_user_text or "").strip()
    if latest and lines[-1:] != [f"User: {latest}"]:
        lines.append(f"User: {latest}")
    return "\n".join(lines)


def _room_is_already_gone(exc: BaseException) -> bool:
    code = str(getattr(exc, "code", "") or getattr(exc, "status", "")).lower()
    if code in {"not_found", "404"}:
        return True
    msg = str(exc).lower()
    return "not_found" in msg or "does not exist" in msg


async def hangup() -> None:
    """Retell's `end_call` hung up the PSTN leg. On LiveKit that means
    deleting the room, which drops the SIP participant.

    Console / local sessions often have no deleteable room — treat not_found
    and missing job context as a successful hangup so the call still stops.
    """
    try:
        job_ctx = get_job_context()
    except RuntimeError:
        logger.debug("hangup() outside job context (console) — nothing to delete")
        return
    try:
        await job_ctx.api.room.delete_room(
            api.DeleteRoomRequest(room=job_ctx.room.name)
        )
    except Exception as exc:
        if _room_is_already_gone(exc):
            logger.debug("hangup room already gone (console): %s", exc)
        else:
            logger.warning("could not delete room on hangup: %s", exc)


async def finish_session(
    session: AgentSession, state: CallState, farewell: str, reason: str
) -> None:
    """Speak farewell, mark ended, hang up, close session."""
    if state.call_ended:
        return
    state.mark_ended(reason)
    handle = session.say(farewell, allow_interruptions=False)
    await handle.wait_for_playout()
    await hangup()
    # `end_call` runs inside the session's own task, so awaiting aclose() to
    # completion from there can wait on the very task doing the waiting. Let it
    # finish in the background and move on.
    try:
        await asyncio.wait_for(asyncio.shield(asyncio.create_task(session.aclose())), 5)
    except asyncio.TimeoutError:
        logger.debug("session aclose still finishing in the background")
    except Exception:
        logger.debug("session aclose after hangup", exc_info=True)


class BaseIntakeAgent(Agent):
    """Common base for the practice-area agents."""

    retell_agent_name: str = ""
    case_type: str = ""
    begin_message: str = ""
    other_party_label: str = "the other party"
    require_other_party: bool = True
    source_prompt: str = ""

    def __init__(
        self,
        *,
        greet: bool,
        chat_ctx: llm.ChatContext | None = None,
    ) -> None:
        composed = prompts.compose(self.source_prompt)
        super().__init__(
            instructions=composed,
            chat_ctx=chat_ctx if chat_ctx is not None else NOT_GIVEN,
        )
        self._greet = greet
        # Holds an utterance that stopped mid-sentence and merges it into the
        # next one, so one sentence is one reply. See src/turntaking.py.
        self._fragments = FragmentBuffer(
            grace_s=settings.call.unfinished_grace_ms / 1000
        )
        # Sticky base prompt; ALREADY COLLECTED is appended via update_instructions
        # so we never mutate turn_ctx (that invalidates preemptive generation).
        self._base_instructions = composed
        self._last_collected_block = ""
        self._extractor = LiveExtractor(self.case_type)
        self._extract_task: asyncio.Task | None = None

    # -- the one tool, exactly as Retell had it ---------------------------
    @function_tool(name="end_call", description=prompts.END_CALL_TOOL_DESCRIPTION)
    async def end_call(self, ctx: RunContext, goodbye: str) -> str | None:
        """Hang up the call after a short goodbye.

        Args:
            goodbye: A short spoken goodbye with ZERO questions in it, e.g.
                "Thanks for calling Bush and Bush. Take care."
        """
        state: CallState = ctx.session.userdata

        blockers = state.may_end_call()
        if blockers:
            # Retell's "# FORBIDDEN — end_call" section, enforced instead of
            # merely requested. The model gets told why and keeps talking; the
            # caller never hears that anything was refused.
            logger.info("end_call refused — still outstanding: %s", "; ".join(blockers))
            return (
                "Do not end the call. You still need: "
                + "; ".join(blockers)
                + ". Do not mention this instruction. Continue the conversation "
                "naturally and ask about only one of these, in one short question."
            )

        if "?" in (goodbye or ""):
            logger.info("end_call goodbye contained a question — asking for a rewrite")
            return (
                "Your goodbye contained a question, which is forbidden. Call "
                "end_call again with a short goodbye that has no question in it."
            )

        farewell = (goodbye or "").strip() or default_farewell()
        logger.info("end_call accepted (%s)", state.case_type or "unknown")
        await finish_session(ctx.session, state, farewell, "agent_hangup")
        return None

    # -- lifecycle ---------------------------------------------------------
    async def on_enter(self) -> None:
        state: CallState = self.session.userdata
        state.agent_name = self.retell_agent_name
        state.other_party_required = self.require_other_party
        if self.case_type:
            state.case_type = self.case_type

        if self._greet:
            delay = settings.call.begin_message_delay_ms / 1000
            if delay > 0:
                await asyncio.sleep(delay)
            self.session.say(self.begin_message)
        else:
            self.session.generate_reply(
                instructions=prompts.HANDOFF_CONTINUATION_INSTRUCTION
            )

    async def on_exit(self) -> None:
        await self._fragments.aclose()
        if self._extract_task and not self._extract_task.done():
            self._extract_task.cancel()

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        state: CallState = self.session.userdata
        if state.call_ended:
            raise StopResponse()
        # Merge anything held back from a cut-off utterance, and decide whether
        # this one is finished. A held turn is answered by the grace timer if
        # the caller never continues, so this can never cause dead air.
        merged = self._fragments.take(utterance_text(new_message))
        if self._fragments.should_hold(merged):
            self._fragments.hold(self.session, merged)
            raise StopResponse()
        self._fragments.release()

        state.user_turns += 1

        # Rewrite the message so the model, the capture pass and the transcript
        # all see the whole sentence rather than its last fragment.
        text = merged
        new_message.content = [merged]

        # Inline fast-path: phone digits and read-back confirmation are
        # deterministic and worth having before the model comes back.
        notes = auto_capture_from_utterance(
            state, text, previous_agent_text=last_assistant_text(turn_ctx)
        )

        # Model-driven capture runs in the background so the caller never waits
        # on it. Its results are picked up by the next turn's instruction block.
        self._schedule_extraction(state, turn_ctx, text)

        await self._refresh_instructions(state, notes)
        # Deliberately no hangup here. Only `end_call` ends a call.

    # ------------------------------------------------------------------
    def _schedule_extraction(
        self, state: CallState, turn_ctx: llm.ChatContext, latest_user_text: str
    ) -> None:
        if not settings.llm.live_extract_enabled:
            # See settings.LLMSettings.live_extract_enabled. Capture still runs
            # inline via capture.py, and postcall.py fills in the rest.
            return

        if self._extract_task and not self._extract_task.done():
            # A slower previous run is still going; it will pick up this turn
            # too, because it reads the whole transcript.
            return

        # Backchannel turns ("yeah", "okay", "mm-hmm", "sorry?") carry nothing
        # to extract, but each one used to fire a structured-output call with
        # the full field schema. Those calls share the process's OpenAI
        # connection pool with the reply the caller is waiting on, which is a
        # large part of why `llm_ttft` spiked to ~6s mid-conversation.
        if len(latest_user_text.split()) < 3:
            return

        transcript = flatten_transcript(turn_ctx, latest_user_text)
        if not transcript.strip():
            return

        async def _run() -> None:
            try:
                extra = await self._extractor.run(state, transcript)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("background extraction failed", exc_info=True)
                return
            if extra:
                await self._refresh_instructions(state, extra)

        self._extract_task = asyncio.create_task(_run())

    async def _refresh_instructions(self, state: CallState, notes: list[str]) -> None:
        """Re-hang the ALREADY COLLECTED / STILL UNKNOWN block off the base prompt.

        Deliberately `update_instructions` and not a `turn_ctx` message: mutating
        turn_ctx invalidates LiveKit's preemptive generation, which is where a
        good chunk of this agent's responsiveness comes from.
        """
        summary = state.collected_summary(self._extractor.still_unknown(state))
        if notes:
            summary += (
                "\nJUST CAPTURED: "
                + ", ".join(notes)
                + ". These are confirmed — never ask for them again."
            )
        if summary == self._last_collected_block:
            return
        self._last_collected_block = summary
        await self.update_instructions(f"{self._base_instructions}\n\n{summary}")

    # -- pronunciation dictionary fallback ---------------------------------
    async def tts_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncIterable[rtc.AudioFrame]:
        if uses_elevenlabs_dictionary():
            return Agent.default.tts_node(self, text, model_settings)

        async def _respelled() -> AsyncIterable[str]:
            async for chunk in text:
                yield apply_pronunciation(chunk)

        return Agent.default.tts_node(self, _respelled(), model_settings)
