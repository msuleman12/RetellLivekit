"""Replays the call where the caller gave a number five times and the firm
still got one nobody had said.

    User:  "plus 1 2 2, 3, 45, 56, 78, and 90."
    Agent: "Got it, you said 1-222-345-5678-90 ... Is that right?"
    ...four more attempts...
    record: "user_phone": "2234556789"     <- never spoken by anyone

Two separate failures: `normalize_phone` invented a number out of a garbled
digit run, and once stored it could never be corrected.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.capture import auto_capture_from_utterance  # noqa: E402
from src.state import MAX_PHONE_ATTEMPTS, CallState, normalize_phone  # noqa: E402

GARBLED = "plus 1 2 2, 3, 45, 56, 78, and 90."


class NeverInventANumberTests(unittest.TestCase):
    def test_garbled_run_is_rejected_not_mined(self) -> None:
        self.assertIsNone(normalize_phone(GARBLED))

    def test_nine_digits_plus_country_code_rejected(self) -> None:
        self.assertIsNone(normalize_phone("plus 1 23 56 78 90"))

    def test_impossible_area_code_rejected(self) -> None:
        self.assertIsNone(normalize_phone("1 2 3 4 5 6 7 8 9 0"))

    def test_real_numbers_still_work(self) -> None:
        self.assertEqual(normalize_phone("1 (214) 555-0199"), "2145550199")
        self.assertEqual(
            normalize_phone("two one four five five five zero one nine nine"),
            "2145550199",
        )

    def test_garbled_attempt_stores_nothing_in_the_phone_field(self) -> None:
        state = CallState(case_type="accident")
        auto_capture_from_utterance(state, GARBLED)
        self.assertEqual(state.phone, "", "a number nobody said must not be stored")
        self.assertEqual(state.phone_attempts, 1)


class CallerCanCorrectTheNumberTests(unittest.TestCase):
    def test_unconfirmed_number_is_replaceable(self) -> None:
        state = CallState(case_type="accident")
        auto_capture_from_utterance(state, "my number is 2145550199")
        self.assertEqual(state.phone, "2145550199")
        auto_capture_from_utterance(state, "sorry, it's 9725550142")
        self.assertEqual(state.phone, "9725550142", "a correction must win")

    def test_confirmed_number_is_not_overwritten(self) -> None:
        state = CallState(case_type="accident")
        auto_capture_from_utterance(state, "my number is 2145550199")
        state.phone_read_back = True
        auto_capture_from_utterance(state, "9725550142")
        self.assertEqual(state.phone, "2145550199")


class StopAskingAfterThreeTriesTests(unittest.TestCase):
    def _three_bad_attempts(self) -> CallState:
        state = CallState(case_type="accident")
        for _ in range(MAX_PHONE_ATTEMPTS):
            auto_capture_from_utterance(state, GARBLED)
        return state

    def test_phone_stops_blocking_the_call(self) -> None:
        state = self._three_bad_attempts()
        self.assertTrue(state.phone_unverified)
        self.assertFalse(
            any("callback number" in m for m in state.missing_must_haves()),
            "after three tries the phone must stop blocking the conversation",
        )

    def test_agent_is_told_to_stop_asking(self) -> None:
        summary = self._three_bad_attempts().collected_summary()
        self.assertIn("Stop asking", summary)

    def test_what_the_caller_said_reaches_the_firm(self) -> None:
        collected = self._three_bad_attempts().to_dict()["collected"]
        self.assertEqual(collected["user_phone"], "")
        self.assertEqual(collected["user_phone_unverified"], "122345567890")
        self.assertEqual(collected["user_phone_attempts"], MAX_PHONE_ATTEMPTS)

    def test_two_tries_still_asks(self) -> None:
        state = CallState(case_type="accident")
        for _ in range(MAX_PHONE_ATTEMPTS - 1):
            auto_capture_from_utterance(state, GARBLED)
        self.assertTrue(any("callback number" in m for m in state.missing_must_haves()))


class NoNewTopicsAfterTheCloseTests(unittest.TestCase):
    """The agent said "Take care." and then asked about witnesses, then the
    insurance claim, then the other driver's insurance."""

    def _closing_state(self) -> CallState:
        state = CallState(case_type="accident")
        state.record_name("John", "Smith")
        state.phone = "2145550199"
        state.phone_read_back = True
        state.other_party_name = "I don't know"
        state.incident_summary = "Hit at an intersection, driver left the scene."
        state.callback_promised = True
        state.closing_offered = True
        return state

    def test_menu_is_withdrawn_once_closing(self) -> None:
        state = self._closing_state()
        menu = ("witnesses", "police_report")
        summary = state.collected_summary(menu)
        self.assertNotIn("STILL UNKNOWN", summary)
        self.assertIn("already offered the close", summary)

    def test_menu_is_offered_before_closing(self) -> None:
        state = self._closing_state()
        state.closing_offered = False
        menu = ("witnesses",)
        self.assertIn("STILL UNKNOWN", state.collected_summary(menu))


class TestNumberOverrideTests(unittest.TestCase):
    def test_env_flag_allows_fake_numbers_for_console_testing(self) -> None:
        """So you can test with 123-456-7890 without fighting the validator."""
        import os

        self.assertIsNone(normalize_phone("1 2 3 4 5 6 7 8 9 0"))
        os.environ["ALLOW_TEST_PHONE_NUMBERS"] = "true"
        try:
            self.assertEqual(normalize_phone("1 2 3 4 5 6 7 8 9 0"), "1234567890")
        finally:
            del os.environ["ALLOW_TEST_PHONE_NUMBERS"]
        self.assertIsNone(
            normalize_phone("1 2 3 4 5 6 7 8 9 0"),
            "the relaxation must not leak past the test",
        )


if __name__ == "__main__":
    unittest.main()
