"""The agent talking over the caller with "Are you still there?".

On a live call the reminder fired 165 ms after the agent's own question and
landed on top of the caller's answer:

    07:35:39.837  agent: "...tell me your full first and last name?"
    07:35:40.002  sending silence reminder 1
    07:35:44.547  user:  "My name is John Smith."
    07:35:44.567  agent: "Are you still there? Take your"

Cause: the idle clock only reset on CALLER activity, so the 11 seconds the
agent spent generating and speaking counted as caller silence. The instant the
agent fell quiet, idle was already past the 10s threshold.
"""

from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from src.lifecycle import CallLifecycle  # noqa: E402


class _State:
    call_ended = False
    disconnect_reason = ""
    reminder_count = 0

    def collected_summary(self, *a, **k):
        return "ALREADY COLLECTED"


def _session(agent_state="listening", user_state="listening"):
    s = MagicMock()
    s.agent_state = agent_state
    s.user_state = user_state
    s.generate_reply = MagicMock()
    return s


class AgentSpeechResetsTheIdleClockTests(unittest.TestCase):
    def test_returning_to_listening_resets_idle(self) -> None:
        session = _session()
        lc = CallLifecycle(session, _State())
        lc._last_user_activity = time.time() - 30      # agent talked for 30s
        lc._on_agent_state(MagicMock(new_state="listening"))
        idle = time.time() - lc._last_user_activity
        self.assertLess(idle, 1.0, "idle clock should restart when the agent stops")

    def test_speaking_does_not_reset(self) -> None:
        """Entering `speaking` must not touch the clock — only leaving it does."""
        session = _session()
        lc = CallLifecycle(session, _State())
        lc._user_final_at = None
        stamp = time.time() - 30
        lc._last_user_activity = stamp
        lc._on_agent_state(MagicMock(new_state="speaking"))
        self.assertEqual(lc._last_user_activity, stamp)


class DoesNotNudgeWhileTheCallerIsSpeakingTests(unittest.IsolatedAsyncioTestCase):
    async def _run_once(self, session) -> MagicMock:
        lc = CallLifecycle(session, _State())
        lc._last_user_activity = time.time() - 30     # well past the 10s trigger
        task = asyncio.create_task(lc._silence_watchdog())
        await asyncio.sleep(0.7)
        lc._closed = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return session.generate_reply

    async def test_silent_caller_is_nudged(self) -> None:
        reply = await self._run_once(_session(user_state="listening"))
        self.assertTrue(reply.called, "a genuinely silent caller should be nudged")

    async def test_speaking_caller_is_not_nudged(self) -> None:
        reply = await self._run_once(_session(user_state="speaking"))
        self.assertFalse(
            reply.called,
            "the caller had started answering — nudging here talks over them",
        )

    async def test_agent_still_speaking_is_not_nudged(self) -> None:
        reply = await self._run_once(_session(agent_state="speaking"))
        self.assertFalse(reply.called)


if __name__ == "__main__":
    unittest.main()
