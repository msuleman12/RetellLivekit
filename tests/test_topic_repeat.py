"""A STILL UNKNOWN field must not be suggested for the whole call.

`still_unknown` hands the model the first N unfilled fields in schema order,
and a field leaves that list only when it is recorded or the extractor marks
it asked. In every practice area the top of the list is held by fields whose
schema says "only populate if the caller mentioned it" - email, preferred
contact, best time to reach. Callers rarely volunteer those, so nothing filled
them and nothing marked them asked, and the same names went to the model turn
after turn. Claire asked, got a vague answer or none, saw the name again, and
asked again.

Symptom in a live call: the same question three, four, five times, worst on
employment (all six of its opening suggestions are that kind of field).

Run:

    python -m unittest tests.test_topic_repeat -v
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("LIVEKIT_URL", "wss://test.invalid")
os.environ.setdefault("LIVEKIT_API_KEY", "test")
os.environ.setdefault("LIVEKIT_API_SECRET", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("DEEPGRAM_API_KEY", "test")

from src.extract import LiveExtractor  # noqa: E402
from src.state import MAX_TOPIC_OFFERS, CallState  # noqa: E402

CASES = ("accident", "employment", "premises", "malpractice", "harassment")


def _turn(extractor: LiveExtractor, state: CallState) -> tuple[str, ...]:
    """One caller turn: what the model is shown, and the offer counted."""
    state.user_turns += 1
    unknown = extractor.still_unknown(state)
    state.note_topics_offered(unknown)
    return unknown


class AFieldStandsDownAfterItsTurns(unittest.TestCase):
    def test_no_field_is_suggested_forever(self) -> None:
        for case in CASES:
            with self.subTest(case=case):
                e, s = LiveExtractor(case), CallState(case_type=case)
                seen = [_turn(e, s) for _ in range(12)]
                first = seen[0][0]
                offered_on = sum(1 for turn in seen if first in turn)
                self.assertLessEqual(
                    offered_on,
                    MAX_TOPIC_OFFERS,
                    f"{case}: {first!r} was suggested on {offered_on} turns - "
                    "this is the field the caller kept being asked about",
                )

    def test_the_case_specific_questions_get_a_turn(self) -> None:
        """The passive contact fields must stop crowding out the real ones."""
        for case in CASES:
            with self.subTest(case=case):
                e, s = LiveExtractor(case), CallState(case_type=case)
                opening = set(e.still_unknown(s))
                later: set[str] = set()
                for _ in range(12):
                    later |= set(_turn(e, s))
                self.assertTrue(
                    later - opening,
                    f"{case}: after 12 turns the model has still only ever been "
                    "shown the same opening six names",
                )

    def test_a_recorded_answer_still_removes_the_field(self) -> None:
        e, s = LiveExtractor("accident"), CallState(case_type="accident")
        name = _turn(e, s)[0]
        s.record_optional(name, "some answer")
        self.assertNotIn(name, _turn(e, s))

    def test_an_asked_topic_still_removes_the_field(self) -> None:
        e, s = LiveExtractor("accident"), CallState(case_type="accident")
        name = _turn(e, s)[0]
        s.asked_topics.add(name)
        self.assertNotIn(name, _turn(e, s))

    def test_one_turn_costs_one_offer_however_often_instructions_refresh(self) -> None:
        """Background extraction refreshes the instructions a second time."""
        e, s = LiveExtractor("accident"), CallState(case_type="accident")
        s.user_turns = 1
        for _ in range(4):  # same turn, four refreshes
            s.note_topics_offered(e.still_unknown(s))
        name = e.still_unknown(s)[0]
        self.assertEqual(
            s.topic_offers[name],
            1,
            "a field burned several of its turns on a single utterance",
        )

    def test_menu_stays_within_its_limit(self) -> None:
        e, s = LiveExtractor("employment"), CallState(case_type="employment")
        for _ in range(12):
            self.assertLessEqual(len(_turn(e, s)), 6)


class EveryPromptDefersOnPhoneCompleteness(unittest.TestCase):
    """No prompt may ask Claire to judge what the validator decides."""

    def test_no_prompt_judges_completeness_itself(self) -> None:
        import re

        from src import prompts

        for case in CASES:
            with self.subTest(case=case):
                text = " ".join(getattr(prompts, case.upper() + "_PROMPT").split())
                self.assertIsNone(
                    re.search(r"[Ii]ncomplete phone|ask again once", text),
                    f"{case}: the prompt forbids counting digits and then asks "
                    "Claire to decide the number is incomplete",
                )
                self.assertRegex(
                    text,
                    r"[Aa]sk (again|for the number again)[^.]*STILL UNKNOWN",
                    f"{case}: nothing tells Claire when re-asking is allowed",
                )


if __name__ == "__main__":
    unittest.main()
