"""The behaviours this port was missing: a caller-driven close, a model-driven
capture path, and a phone number that is actually dialable.

Run:

    python -m unittest tests.test_conversational -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import prompts  # noqa: E402
from src.capture import auto_capture_from_utterance, extract_name  # noqa: E402
from src.extract import LiveExtractor  # noqa: E402
from src.state import CallState, is_valid_us_number, normalize_phone  # noqa: E402


def _complete_state() -> CallState:
    state = CallState(case_type="accident")
    state.record_name("Jordan", "Lee")
    state.record_phone("2145550199")
    state.phone_read_back = True
    state.other_party_name = "Alex Rivera"
    state.incident_summary = "Rear-ended on Preston Road last Tuesday."
    state.callback_promised = True
    state.closing_offered = True
    return state


class PhoneValidityTests(unittest.TestCase):
    """A real call produced `1290909490` and the firm would have dialed it."""

    def test_country_code_plus_nine_digits_is_not_a_number(self) -> None:
        spoken = "It's plus 1 2 9 0 9 0 9 4 9 0."
        self.assertIsNone(normalize_phone(spoken))

    def test_area_code_may_not_start_with_zero_or_one(self) -> None:
        self.assertFalse(is_valid_us_number("1290909490"))
        self.assertFalse(is_valid_us_number("0145550199"))
        self.assertTrue(is_valid_us_number("2145550199"))

    def test_exchange_may_not_start_with_zero_or_one(self) -> None:
        self.assertFalse(is_valid_us_number("2141550199"))

    def test_real_numbers_still_pass(self) -> None:
        self.assertEqual(normalize_phone("1 (214) 555-0199"), "2145550199")
        self.assertEqual(
            normalize_phone("two one four five five five zero one nine nine"),
            "2145550199",
        )


class BareNameAnswerTests(unittest.TestCase):
    """"Yes. Moss Ali." matched nothing, so Claire kept asking for the name."""

    def test_bare_first_last(self) -> None:
        self.assertEqual(extract_name("Moss Ali."), ("Moss", "Ali"))

    def test_affirmation_then_name(self) -> None:
        self.assertEqual(extract_name("Yes. Moss Ali."), ("Moss", "Ali"))
        self.assertEqual(extract_name("It's Jordan Lee"), ("Jordan", "Lee"))

    def test_does_not_swallow_pleasantries(self) -> None:
        for phrase in ("Thank you", "No questions", "Car accident", "I'm good"):
            with self.subTest(phrase=phrase):
                self.assertIsNone(extract_name(phrase))

    def test_does_not_read_a_phone_number_as_a_name(self) -> None:
        self.assertIsNone(extract_name("2 1 4 5 5 5 0 1 9 9"))


class EndCallGateTests(unittest.TestCase):
    """Retell's "# FORBIDDEN — end_call" rules, enforced instead of requested."""

    def test_incomplete_intake_blocks_the_tool(self) -> None:
        state = CallState(case_type="accident")
        blockers = state.may_end_call()
        self.assertTrue(blockers)
        self.assertTrue(any("first name" in b for b in blockers))

    def test_complete_intake_still_blocks_until_the_caller_is_done(self) -> None:
        state = _complete_state()
        blockers = state.may_end_call()
        self.assertEqual(len(blockers), 1)
        self.assertIn("not yet said they are finished", blockers[0])

    def test_unlocked_once_the_caller_signs_off(self) -> None:
        state = _complete_state()
        auto_capture_from_utterance(state, "No, that's all. Thanks.")
        self.assertTrue(state.caller_done)
        self.assertEqual(state.may_end_call(), [])

    def test_caller_done_needs_the_close_to_have_been_offered(self) -> None:
        """"That's all" mid-story must not unlock the hangup."""
        state = _complete_state()
        state.closing_offered = False
        auto_capture_from_utterance(state, "No, that's all. Thanks.")
        self.assertFalse(state.caller_done)
        self.assertTrue(state.may_end_call())


class GuidanceIsNotAScriptTests(unittest.TestCase):
    def test_complete_intake_tells_the_agent_to_keep_listening(self) -> None:
        guidance = _complete_state().next_missing_prompt()
        self.assertIn("Do NOT hang up", guidance)
        self.assertIn("let them lead", guidance)

    def test_answered_topics_are_named_not_described(self) -> None:
        state = CallState(case_type="accident")
        menu = LiveExtractor("accident").still_unknown(state)
        self.assertTrue(all(" " not in t for t in menu), "field names only")

    def test_optional_topics_are_offered_as_a_menu(self) -> None:
        state = CallState(case_type="accident")
        menu = LiveExtractor("accident").still_unknown(state)
        self.assertTrue(menu)
        summary = state.collected_summary(menu)
        self.assertIn("STILL UNKNOWN:", summary)
        # Names only. The old version pasted a full sentence of description per
        # topic into every single request.
        self.assertNotIn("—", summary)
        self.assertLess(len(summary), 600, "the per-turn note must stay small")

    def test_answered_topics_leave_the_menu(self) -> None:
        state = CallState(case_type="accident")
        extractor = LiveExtractor("accident")
        self.assertTrue(any(m.startswith("police_report") for m in extractor.still_unknown(state, limit=99)))
        state.record_optional("police_report", True)
        self.assertFalse(any(m.startswith("police_report") for m in extractor.still_unknown(state, limit=99)))

    def test_prompt_stays_close_to_the_retell_original(self) -> None:
        """The appended guidance must not swamp Retell's own prompt.

        It reached 2.4x the original, 45 prohibition lines, with "read the
        phone back once" stated in four different blocks. That is why the agent
        stopped sounding like a receptionist."""
        composed = prompts.compose(prompts.ACCIDENT_PROMPT)
        ratio = len(composed) / len(prompts.ACCIDENT_PROMPT)
        self.assertLess(ratio, 1.7, f"appended guidance has grown again ({ratio:.1f}x)")
        self.assertIn("Follow the caller", composed)
        self.assertIn("never a queue to work", composed)

    def test_appended_blocks_do_not_restate_the_retell_prompt(self) -> None:
        """The rule that keeps this from growing back.

        Every block appended below the Retell prompt exists only to say
        something Retell's own text does not. When that rule lapsed, "read the
        phone back once" ended up stated in four separate places and the agent
        started behaving like a rule-follower instead of a receptionist.
        """
        import re

        retell = prompts.ACCIDENT_PROMPT.lower()
        appended = (prompts.DELIVERY_BLOCK + prompts.OPERATING_BLOCK).lower()
        for label, pattern in (
            ("reading the number back", r"read.{0,8}back"),
            ("one question per turn", r"one question per turn"),
            ("never talking over the caller", r"talk over"),
            ("no legal advice", r"legal advice"),
            ("the end_call preconditions", r"forbidden"),
        ):
            with self.subTest(instruction=label):
                if re.search(pattern, retell):
                    self.assertIsNone(
                        re.search(pattern, appended),
                        f"{label!r} is already in the Retell prompt - "
                        "the appended blocks must not repeat it",
                    )


class LiveExtractorMergeTests(unittest.TestCase):
    def test_merges_the_retell_field_names(self) -> None:
        state = CallState(case_type="accident")
        notes = LiveExtractor("accident")._merge(
            state,
            {
                "user_fname": "moss",
                "user_lname": "ali",
                "user_phone": "+1 214 555 0199",
                "phone_read_back_confirmed": True,
                "other_party_name": "State Farm",
                "incident_summary": "Rear-ended at a red light on Preston Road.",
                "agent_offered_the_close": False,
                "caller_said_they_are_finished": False,
                "topics_already_raised": ["police_report"],
                "accident_injuries": "Neck pain, ongoing",
                "police_report": None,
            },
        )
        self.assertEqual(state.full_name, "Moss Ali")
        self.assertEqual(state.phone, "2145550199")
        self.assertTrue(state.phone_read_back)
        self.assertEqual(state.other_party_name, "State Farm")
        self.assertEqual(state.optional_fields["accident_injuries"], "Neck pain, ongoing")
        self.assertIn("police_report", state.asked_topics)
        self.assertTrue(any("accident_injuries" in n for n in notes))

    def test_refuses_the_callers_own_name_as_the_other_party(self) -> None:
        state = CallState(case_type="accident")
        state.record_name("Jordan", "Lee")
        LiveExtractor("accident")._merge(state, {"other_party_name": "Jordan Lee"})
        self.assertEqual(state.other_party_name, "")

    def test_refuses_the_firm_as_the_other_party(self) -> None:
        state = CallState(case_type="accident")
        LiveExtractor("accident")._merge(state, {"other_party_name": "Bush and Bush"})
        self.assertEqual(state.other_party_name, "")

    def test_caller_who_starts_talking_again_reopens_the_call(self) -> None:
        state = _complete_state()
        state.caller_done = True
        LiveExtractor("accident")._merge(
            state, {"caller_said_they_are_finished": False}
        )
        self.assertFalse(state.caller_done)
        self.assertTrue(state.may_end_call())


class PostCallPhoneTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_model_phone_is_dropped(self) -> None:
        from src import postcall

        state = CallState(case_type="accident")
        session = MagicMock()
        session.history.items = []

        fake = MagicMock()
        fake.choices = [MagicMock()]
        fake.choices[0].message.content = (
            '{"user_fname": "Moss", "user_lname": "Ali", '
            '"user_phone": "1290909490", "call_summary": "x", '
            '"user_sentiment": null, "call_successful": true, "in_voicemail": false}'
        )
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=fake)

        with patch("openai.AsyncOpenAI", return_value=client):
            payload = await postcall.analyze(
                session,
                state,
                transcript="User: hi\nAgent: hello",
                transcript_object=[],
            )

        custom = payload["call"]["call_analysis"]["custom_analysis_data"]
        self.assertEqual(custom.get("user_fname"), "Moss")
        self.assertNotIn("user_phone", custom)


if __name__ == "__main__":
    unittest.main()


class ExtractorResilienceTests(unittest.IsolatedAsyncioTestCase):
    """The regex fast-path is the safety net, so a broken extractor must be
    survivable rather than fatal — and must not spam the log every turn."""

    async def test_transient_failures_are_retried(self) -> None:
        ex = LiveExtractor("accident")
        state = CallState(case_type="accident")
        with patch.object(ex, "_call_model", AsyncMock(side_effect=RuntimeError("boom"))):
            await ex.run(state, "User: hi")
            self.assertFalse(ex._disabled)
            await ex.run(state, "User: hi")
            self.assertFalse(ex._disabled)
            await ex.run(state, "User: hi")
        self.assertTrue(ex._disabled)

    async def test_disabled_extractor_returns_quietly(self) -> None:
        ex = LiveExtractor("accident")
        ex._disabled = True
        self.assertEqual(await ex.run(CallState(), "User: hi"), [])

    async def test_a_good_turn_resets_the_failure_count(self) -> None:
        ex = LiveExtractor("accident")
        state = CallState(case_type="accident")
        with patch.object(ex, "_call_model", AsyncMock(side_effect=RuntimeError("boom"))):
            await ex.run(state, "User: hi")
        self.assertEqual(ex._consecutive_failures, 1)
        with patch.object(ex, "_call_model", AsyncMock(return_value={"user_fname": "Moss"})):
            await ex.run(state, "User: hi")
        self.assertEqual(ex._consecutive_failures, 0)
        self.assertEqual(state.first_name, "Moss")


class TranscriptAssemblyTests(unittest.TestCase):
    """The turn that just finished is not in `turn_ctx` yet, and it is the one
    carrying the answer — leaving it out classified and captured a turn late."""

    @staticmethod
    def _ctx(*pairs):
        items = []
        for role, text in pairs:
            item = MagicMock()
            item.role = role
            item.text_content = text
            items.append(item)
        ctx = MagicMock()
        ctx.items = items
        return ctx

    def test_latest_user_turn_is_appended(self) -> None:
        from src.agents.base import BaseIntakeAgent

        ctx = self._ctx(("assistant", "How can I help?"))
        out = BaseIntakeAgent._transcript_from(ctx, "I was rear-ended")
        self.assertEqual(out, "Agent: How can I help?\nUser: I was rear-ended")

    def test_not_duplicated_when_already_present(self) -> None:
        from src.agents.base import BaseIntakeAgent

        ctx = self._ctx(("assistant", "How can I help?"), ("user", "I was rear-ended"))
        out = BaseIntakeAgent._transcript_from(ctx, "I was rear-ended")
        self.assertEqual(out.count("I was rear-ended"), 1)

    def test_router_assembles_the_same_way(self) -> None:
        from src.agents.router import RouterAgent

        ctx = self._ctx(("assistant", "What brings you in?"))
        out = RouterAgent._transcript(ctx, "my boss stopped paying me")
        self.assertTrue(out.endswith("User: my boss stopped paying me"))
