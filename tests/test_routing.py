"""Unit tests for keyword case-type routing (no transfer tools)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.routing import classify_case_type  # noqa: E402


class ClassifyCaseTypeTests(unittest.TestCase):
    def test_accident(self) -> None:
        self.assertEqual(
            classify_case_type("I was in a car accident yesterday"),
            "accident",
        )
        self.assertEqual(classify_case_type("someone hit my car"), "accident")

    def test_employment_before_premises(self) -> None:
        self.assertEqual(
            classify_case_type("I got hurt at work and my employer won't help"),
            "employment",
        )

    def test_premises(self) -> None:
        self.assertEqual(
            classify_case_type("I slipped and fell at the grocery store"),
            "premises",
        )

    def test_harassment(self) -> None:
        self.assertEqual(
            classify_case_type("I'm calling about sexual harassment at my job"),
            "harassment",
        )

    def test_malpractice(self) -> None:
        self.assertEqual(
            classify_case_type("This is about medical malpractice by my doctor"),
            "malpractice",
        )

    def test_unclear_returns_none(self) -> None:
        self.assertIsNone(classify_case_type("I need a lawyer"))
        self.assertIsNone(classify_case_type(""))


if __name__ == "__main__":
    unittest.main()


class LlmClassificationTests(unittest.IsolatedAsyncioTestCase):
    """Retell's extract node was a model, not a word list.

    These are the phrasings the keyword pass cannot place, and which the old
    port turned into a redundant clarifying question or an out-of-scope decline.
    """

    @staticmethod
    def _client(value: str):
        from unittest.mock import AsyncMock, MagicMock

        reply = MagicMock()
        reply.choices = [MagicMock()]
        reply.choices[0].message.content = f'{{"case_type": "{value}"}}'
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=reply)
        return client

    async def test_keyword_pass_leaves_natural_phrasing_to_the_model(self) -> None:
        for phrase in (
            "my boss stopped paying me overtime",
            "I got hurt in a store last week",
            "the surgeon left something inside me",
        ):
            with self.subTest(phrase=phrase):
                self.assertIsNone(classify_case_type(phrase))

    async def test_model_routes_what_the_keywords_missed(self) -> None:
        from unittest.mock import patch

        from src.routing import classify_case_type_llm

        with patch("openai.AsyncOpenAI", return_value=self._client("employment")):
            verdict = await classify_case_type_llm(
                "User: my boss stopped paying me overtime"
            )
        self.assertEqual(verdict, "employment")

    async def test_empty_transcript_short_circuits(self) -> None:
        from src.routing import classify_case_type_llm

        self.assertIsNone(await classify_case_type_llm("   "))

    async def test_failure_falls_back_to_clarifying(self) -> None:
        from unittest.mock import patch

        from src.routing import classify_case_type_llm

        with patch("openai.AsyncOpenAI", side_effect=RuntimeError("no key")):
            self.assertIsNone(await classify_case_type_llm("User: something"))
