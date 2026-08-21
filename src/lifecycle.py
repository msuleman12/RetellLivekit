"""Call-level timers that Retell provided as agent settings.

    reminder_trigger_ms       10000   -> nudge the caller after 10s of silence
    reminder_max_count        2       -> at most twice per silence
    max_call_duration_ms      664000  -> hard stop
    end_call_after_silence_ms 261000  -> hang up on a dead line

LiveKit has none of these built in, so they are implemented here as a small
watchdog attached to the session.
"""

from __future__ import annotations

import asyncio
import logging
import time

from livekit.agents import AgentSession

from . import settings
from .state import CallState

logger = logging.getLogger("bushbush.lifecycle")

REMINDER_INSTRUCTION = (
    "The caller has gone quiet. In ONE short warm sentence, check they are still "
    "there. Do NOT re-ask their name, phone, or any ALREADY COLLECTED field. "
    "Do not start a new topic and do not add a second question."
)


class CallLifecycle:
    def __init__(self, session: AgentSession, state: CallState) -> None:
        self._session = session
        self._state = state
        self._last_user_activity = time.time()
        self._reminders_sent = 0
        self._tasks: list[asyncio.Task] = []
        self._closed = False
        # Wall time of the latest final user transcript; used for reply-latency logs.
        self._user_final_at: float | None = None
        # Per-turn timing parts, keyed by speech_id, assembled from
        # `metrics_collected` and logged as one line once TTS reports in.
        self._turn_parts: dict[str, dict] = {}

    # ------------------------------------------------------------------
    def start(self) -> None:
        self._session.on("user_input_transcribed", self._on_user_input)
        self._session.on("user_state_changed", self._on_user_state)
        self._session.on("conversation_item_added", self._on_item_added)
        self._session.on("agent_state_changed", self._on_agent_state)
        self._session.on("metrics_collected", self._on_metrics)

        loop = asyncio.get_event_loop()
        self._tasks.append(loop.create_task(self._silence_watchdog()))
        self._tasks.append(loop.create_task(self._max_duration_watchdog()))

    async def aclose(self) -> None:
        self._closed = True
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: B014
                pass
        self._tasks.clear()

    # -- activity tracking ---------------------------------------------
    def _touch(self) -> None:
        self._last_user_activity = time.time()
        self._reminders_sent = 0

    def _on_user_input(self, ev) -> None:  # UserInputTranscribedEvent
        if getattr(ev, "transcript", "").strip():
            self._touch()
            if getattr(ev, "is_final", False):
                self._user_final_at = time.time()

    def _on_user_state(self, ev) -> None:  # UserStateChangedEvent
        if getattr(ev, "new_state", None) == "speaking":
            self._touch()

    def _on_item_added(self, ev) -> None:  # ConversationItemAddedEvent
        item = getattr(ev, "item", None)
        if item is not None and getattr(item, "role", None) == "user":
            self._touch()

    def _on_agent_state(self, ev) -> None:  # AgentStateChangedEvent
        if getattr(ev, "new_state", None) != "speaking":
            return
        started = self._user_final_at
        if started is None:
            return
        self._user_final_at = None
        delta_ms = (time.time() - started) * 1000
        logger.info(
            "reply_latency_ms=%.0f profile=%s",
            delta_ms,
            settings.call.latency_profile,
        )

    # -- per-turn timing breakdown --------------------------------------
    #
    # The console UI paints these numbers in a status bar that scrolls away and
    # never reaches the log file. Every metric below is reported by LiveKit in
    # SECONDS; they are printed as ms to match the status bar.
    #
    # The two TTS numbers answer different questions:
    #   tts_wait (acquire_time) - how long the request sat waiting for a free
    #                             TTS connection before it was even sent. Large
    #                             here means the pool is the bottleneck, not
    #                             ElevenLabs.
    #   tts_ttfb                - how long ElevenLabs took to return the first
    #                             audio byte once the request was on the wire.
    # `conn_reused=False` means a fresh websocket had to be opened, which is
    # where a one-off ~1s tts_ttfb usually comes from.
    @staticmethod
    def _ms(value) -> str:
        return "-" if value is None else f"{value * 1000:.0f}ms"

    def _on_metrics(self, ev) -> None:
        try:
            m = getattr(ev, "metrics", None)
            if m is None:
                return
            kind = type(m).__name__
            sid = getattr(m, "speech_id", None) or "adhoc"

            if kind == "EOUMetrics":
                part = self._turn_parts.setdefault(sid, {})
                part["eou"] = getattr(m, "end_of_utterance_delay", None)
                part["stt"] = getattr(m, "transcription_delay", None)
                part["cb"] = getattr(m, "on_user_turn_completed_delay", None)

            elif kind == "LLMMetrics":
                if getattr(m, "cancelled", False):
                    return
                part = self._turn_parts.setdefault(sid, {})
                # A turn can make more than one LLM call (tool steps); the first
                # is the one the caller is waiting on.
                part.setdefault("llm_ttft", getattr(m, "ttft", None))

            elif kind == "TTSMetrics":
                if getattr(m, "cancelled", False):
                    return
                part = self._turn_parts.setdefault(sid, {})
                if part.get("logged"):
                    return  # later sentences of the same reply; only the first matters
                part["tts_ttfb"] = getattr(m, "ttfb", None)
                part["tts_wait"] = getattr(m, "acquire_time", None)
                part["tts_reused"] = getattr(m, "connection_reused", None)
                self._log_turn(sid)
        except Exception:  # pragma: no cover - never break a call over a log line
            logger.debug("turn metrics line failed", exc_info=True)

    def _log_turn(self, sid: str) -> None:
        part = self._turn_parts.get(sid)
        if not part or part.get("logged"):
            return
        # Kept rather than popped, so the remaining sentences of this same reply
        # can see the flag and stay quiet. Trim so a long call cannot grow it
        # without bound.
        part["logged"] = True
        while len(self._turn_parts) > 20:
            self._turn_parts.pop(next(iter(self._turn_parts)))
        spoke_after = sum(
            v for v in (part.get("eou"), part.get("llm_ttft"), part.get("tts_ttfb"))
            if isinstance(v, (int, float))
        )
        logger.info(
            "turn timing | eou %s (stt %s + cb %s) | llm_ttft %s | "
            "tts_wait %s -> tts_ttfb %s (conn_reused=%s) | caller waited %s",
            self._ms(part.get("eou")),
            self._ms(part.get("stt")),
            self._ms(part.get("cb")),
            self._ms(part.get("llm_ttft")),
            self._ms(part.get("tts_wait")),
            self._ms(part.get("tts_ttfb")),
            part.get("tts_reused"),
            self._ms(spoke_after),
        )

    # -- watchdogs ------------------------------------------------------
    async def _silence_watchdog(self) -> None:
        reminder_after = settings.call.reminder_trigger_ms / 1000
        max_reminders = settings.call.reminder_max_count
        hangup_after = settings.call.end_call_after_silence_ms / 1000

        try:
            while not self._closed:
                await asyncio.sleep(0.5)
                if self._state.call_ended or self._state.disconnect_reason:
                    self._closed = True
                    return

                idle = time.time() - self._last_user_activity

                if idle >= hangup_after:
                    logger.info("hanging up after %.0fs of silence", idle)
                    self._state.mark_ended("silence_timeout")
                    await self._hangup()
                    return

                if self._session.agent_state != "listening":
                    # the agent is thinking or speaking; not really silence
                    continue

                due = reminder_after * (self._reminders_sent + 1)
                if self._reminders_sent < max_reminders and idle >= due:
                    self._reminders_sent += 1
                    self._state.reminder_count += 1
                    logger.debug("sending silence reminder %d", self._reminders_sent)
                    self._session.generate_reply(
                        instructions=(
                            REMINDER_INSTRUCTION
                            + "\n"
                            + self._state.collected_summary()
                        )
                    )
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception:  # pragma: no cover
            logger.exception("silence watchdog failed")

    async def _max_duration_watchdog(self) -> None:
        limit = settings.call.max_call_duration_ms / 1000
        try:
            await asyncio.sleep(limit)
            if self._closed or self._state.call_ended:
                return
            logger.info("max call duration of %.0fs reached", limit)
            self._state.mark_ended("max_duration_reached")
            try:
                handle = self._session.say(
                    "I'm sorry, I have to let you go here - someone from the firm "
                    "will call you back. Take care."
                )
                await handle.wait_for_playout()
            except Exception:  # pragma: no cover
                pass
            await self._hangup()
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception:  # pragma: no cover
            logger.exception("max duration watchdog failed")

    async def _hangup(self) -> None:
        from .agents.base import hangup

        self._closed = True
        self._state.call_ended = True
        await hangup()
        try:
            await self._session.aclose()
        except Exception:
            logger.debug("session aclose after lifecycle hangup", exc_info=True)
