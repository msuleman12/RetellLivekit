"""Call behavior: phone normalize, end_call gate, call_ended stops reminders.

Run:

    python -m unittest tests.test_call_behavior -v
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.lifecycle import CallLifecycle  # noqa: E402
from src.state import (  # noqa: E402
    CallState,
    extract_phone_digits,
    normalize_phone,
    phone_digit_count,
)


class PhoneNormalizeTests(unittest.TestCase):
    def test_spoken_digits(self) -> None:
        self.assertEqual(
            extract_phone_digits("two one four five five five zero one nine nine"),
            "2145550199",
        )

    def test_oh_as_zero(self) -> None:
        self.assertEqual(extract_phone_digits("two one four oh oh five"), "214005")

    def test_normalize_ten_digits(self) -> None:
        self.assertEqual(normalize_phone("214-555-0199"), "2145550199")
        self.assertEqual(normalize_phone("1 (214) 555-0199"), "2145550199")

    def test_seven_digit_rejected(self) -> None:
        self.assertIsNone(normalize_phone("5551234"))
        self.assertEqual(phone_digit_count("5551234"), 7)

    def test_double_five(self) -> None:
        self.assertEqual(
            extract_phone_digits("two one four double five five zero one nine nine"),
            "2145550199",
        )


class CollectedSummaryTests(unittest.TestCase):
    def test_summary_lists_missing_and_next(self) -> None:
        state = CallState()
        text = state.collected_summary()
        self.assertIn("ALREADY COLLECTED", text)
        self.assertIn("name: (not yet)", text)
        # The block used to say "Next: ask only for the first missing
        # must-have", which turned the call into a questionnaire. It is now
        # soft guidance the model may reorder around the conversation.
        # Facts only now - the rules live in OPERATING_BLOCK, stated once.
        self.assertNotIn("CRITICAL", text)
        self.assertNotIn("Guidance:", text)

    def test_summary_after_name_and_phone(self) -> None:
        state = CallState()
        state.record_name("Jordan", "Lee")
        state.record_phone("2145550199")
        state.phone_read_back = True
        text = state.collected_summary()
        self.assertIn("Jordan Lee", text)
        self.assertIn("2145550199", text)
        self.assertIn("confirmed", text)
        self.assertNotIn("name: (not yet)", text)


class EndCallGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_finish_session_soft_blocks_when_must_haves_missing(self) -> None:
        from src.agents.base import finish_session
        from src.capture import should_hangup_after_capture

        state = CallState(case_type="accident")
        self.assertFalse(should_hangup_after_capture(state))
        session = MagicMock()
        session.say = MagicMock()
        session.aclose = AsyncMock()
        # Incomplete intake must not hang up via should_hangup helper.
        self.assertFalse(should_hangup_after_capture(state))
        self.assertFalse(state.call_ended)
        session.say.assert_not_called()

    async def test_hangup_when_intake_complete_and_caller_done(self) -> None:
        from src.capture import auto_capture_from_utterance, should_hangup_after_capture

        state = CallState(case_type="accident")
        state.record_name("Jordan", "Lee")
        state.record_phone("2145550199")
        state.phone_read_back = True
        state.other_party_name = "Alex"
        state.incident_summary = "Rear-ended on Preston Road last Tuesday afternoon."
        notes = auto_capture_from_utterance(state, "No, that's all, thank you")
        self.assertTrue(state.callback_promised)
        self.assertTrue(should_hangup_after_capture(state))
        self.assertTrue(any("callback_promised" in n for n in notes))


class LifecycleCallEndedTests(unittest.IsolatedAsyncioTestCase):
    async def test_silence_watchdog_stops_when_call_ended(self) -> None:
        session = MagicMock()
        session.agent_state = "listening"
        session.generate_reply = MagicMock()
        state = CallState()
        lifecycle = CallLifecycle(session, state)
        state.call_ended = True

        # Run one iteration of the watchdog briefly
        task = asyncio.create_task(lifecycle._silence_watchdog())
        await asyncio.sleep(0.6)
        lifecycle._closed = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        session.generate_reply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
