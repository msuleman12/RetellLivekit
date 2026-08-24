"""Tests for auto-capture of name/phone from user utterances.

Run:

    python -m unittest tests.test_capture -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.capture import auto_capture_from_utterance, extract_name  # noqa: E402
from src.state import CallState  # noqa: E402


class ExtractNameTests(unittest.TestCase):
    def test_my_name_is(self) -> None:
        self.assertEqual(extract_name("My name is Jordan Lee."), ("Jordan", "Lee"))

    def test_my_full_name_is(self) -> None:
        self.assertEqual(
            extract_name("My full name is Jordan Lee."), ("Jordan", "Lee")
        )

    def test_rejects_garbage(self) -> None:
        self.assertIsNone(extract_name("I was in a car accident yesterday."))


class AutoCaptureTests(unittest.TestCase):
    def test_captures_name_and_phone(self) -> None:
        state = CallState()
        notes = auto_capture_from_utterance(state, "My name is Jordan Lee.")
        self.assertIn("name=Jordan Lee", notes)
        self.assertEqual(state.full_name, "Jordan Lee")

        notes2 = auto_capture_from_utterance(
            state, "So my best number is 2 1 4 5 5 5 0 1 9 9."
        )
        self.assertTrue(any(n.startswith("phone=") for n in notes2))
        self.assertEqual(state.phone, "2145550199")

    def test_yep_plus_digits_confirms_read_back(self) -> None:
        state = CallState()
        state.phone = "2145550199"
        notes = auto_capture_from_utterance(state, "Yep. 2 1 4 5 5 5 0 1 9 9.")
        self.assertTrue(state.phone_read_back)
        self.assertTrue(any("phone_read_back" in n for n in notes))

    def test_yes_alone_confirms_pending_read_back(self) -> None:
        state = CallState()
        state.phone = "2145550199"
        notes = auto_capture_from_utterance(state, "Yes, that's right.")
        self.assertTrue(state.phone_read_back)
        self.assertTrue(any("phone_read_back" in n for n in notes))

    def test_does_not_reask_when_summary_shows_collected(self) -> None:
        state = CallState()
        auto_capture_from_utterance(state, "My name is Jordan Lee.")
        auto_capture_from_utterance(state, "2145550199")
        state.phone_read_back = True
        summary = state.collected_summary()
        self.assertIn("Jordan Lee", summary)
        self.assertIn("2145550199", summary)
        self.assertNotIn("name: (not yet)", summary)
        # The note is facts only now; "do not re-ask" is stated once in
        # OPERATING_BLOCK rather than shouted on every turn.
        self.assertIn("ALREADY COLLECTED", summary)

    def test_captures_other_party_and_dont_know(self) -> None:
        state = CallState()
        notes = auto_capture_from_utterance(
            state, "The other driver name is Alex Rivera."
        )
        self.assertTrue(any("other_party=" in n for n in notes))
        self.assertIn("Alex Rivera", state.other_party_name)
        self.assertEqual(state.full_name, "")

        state2 = CallState()
        auto_capture_from_utterance(state2, "I don't know their name.")
        self.assertEqual(state2.other_party_name, "I don't know")

    def test_closing_affirmation_marks_callback(self) -> None:
        state = CallState()
        state.record_name("Jordan", "Lee")
        state.record_phone("2145550199")
        state.phone_read_back = True
        state.other_party_name = "Alex"
        state.incident_summary = "Rear-ended on Preston Road last Tuesday afternoon."
        notes = auto_capture_from_utterance(state, "No questions, thanks.")
        self.assertTrue(state.callback_promised)
        self.assertTrue(any("callback_promised" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
