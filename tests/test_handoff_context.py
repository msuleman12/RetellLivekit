"""The routing turn must survive the handoff.

`Agent.chat_ctx` does not contain the message currently being handled:
LiveKit commits a user turn as part of generating the reply to it, and the
routing path raises `StopResponse` instead of replying. So the turn that
triggers routing used to reach neither the new agent's context nor
`session.history` - and that turn is, almost by definition, the one where
the caller says what happened.

Symptom in a live call: the intake agent opens by asking about the thing it
was just told, and the transcript the firm receives has no account of the
incident in the caller's own words.

Run:

    python -m unittest tests.test_handoff_context -v
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# settings.py reads the environment at import time; these are never used to
# reach anything, they only have to be non-empty.
os.environ.setdefault("LIVEKIT_URL", "wss://test.invalid")
os.environ.setdefault("LIVEKIT_API_KEY", "test")
os.environ.setdefault("LIVEKIT_API_SECRET", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("DEEPGRAM_API_KEY", "test")

from livekit.agents import llm  # noqa: E402

from src.agents.router import AGENTS_BY_CASE_TYPE, RouterAgent  # noqa: E402
from src.state import CallState  # noqa: E402

GREETING = "Hi, thanks for calling Bush and Bush Law Group - this is Claire."
INCIDENT = "I got rear-ended on the 405 last Tuesday and my neck's been killing me."


class _FakeSession:
    def __init__(self, history: llm.ChatContext) -> None:
        self.history = history
        self.userdata = CallState()
        self.agent = None

    def update_agent(self, agent) -> None:
        self.agent = agent

    def _conversation_item_added(self, message: llm.ChatMessage) -> None:
        self.history.insert(message)


class _StubRouter:
    """The real RouterAgent handoff methods, without constructing an LLM."""

    _seed_chat_ctx = RouterAgent._seed_chat_ctx
    _commit_to_history = RouterAgent._commit_to_history
    _handoff = RouterAgent._handoff

    def __init__(self, ctx: llm.ChatContext, session: _FakeSession) -> None:
        self._ctx = ctx
        self.session = session

    @property
    def chat_ctx(self) -> llm.ChatContext:
        return self._ctx


def _texts(ctx: llm.ChatContext) -> list[str]:
    return [i.text_content or "" for i in ctx.items if i.type == "message"]


def _fresh() -> tuple[_StubRouter, _FakeSession, llm.ChatMessage]:
    ctx = llm.ChatContext.empty()
    ctx.items.append(llm.ChatMessage(role="assistant", content=[GREETING]))
    session = _FakeSession(ctx.copy())
    pending = llm.ChatMessage(role="user", content=[INCIDENT])
    return _StubRouter(ctx, session), session, pending


class HandoffCarriesTheTurnInFlight(unittest.TestCase):
    def test_new_agent_sees_the_routing_turn(self) -> None:
        router, session, pending = _fresh()
        router._handoff(session.userdata, "accident", pending_user_message=pending)

        self.assertIsNotNone(session.agent, "no agent was handed off to")
        self.assertTrue(
            any(INCIDENT in t for t in _texts(session.agent.chat_ctx)),
            "the intake agent cannot see what the caller just described, so it "
            "will ask about it again",
        )

    def test_transcript_sees_the_routing_turn(self) -> None:
        router, session, pending = _fresh()
        router._handoff(session.userdata, "accident", pending_user_message=pending)

        self.assertTrue(
            any(INCIDENT in t for t in _texts(session.history)),
            "postcall.build_transcript reads session.history, so the firm's "
            "record would not contain the caller's account of the incident",
        )

    def test_earlier_history_is_preserved(self) -> None:
        router, session, pending = _fresh()
        router._handoff(session.userdata, "accident", pending_user_message=pending)
        self.assertTrue(any(GREETING in t for t in _texts(session.agent.chat_ctx)))

    def test_already_committed_turn_is_not_duplicated(self) -> None:
        """The late-verdict path routes on a turn that did get a reply."""
        router, session, pending = _fresh()
        session.history.insert(pending)
        before = len(session.history.items)

        router._commit_to_history(pending)

        self.assertEqual(
            len(session.history.items),
            before,
            "the caller would appear to have said it twice",
        )

    def test_seeded_context_does_not_mutate_the_router(self) -> None:
        router, session, pending = _fresh()
        before = len(router.chat_ctx.items)
        router._handoff(session.userdata, "accident", pending_user_message=pending)
        self.assertEqual(len(router.chat_ctx.items), before)

    def test_handoff_without_a_pending_turn(self) -> None:
        """`_apply_late_verdict` routes from a background task with no turn."""
        router, session, _ = _fresh()
        router._handoff(session.userdata, "premises", pending_user_message=None)
        self.assertIsNotNone(session.agent)

    def test_state_records_the_handoff(self) -> None:
        router, session, pending = _fresh()
        started = int(time.time() * 1000)
        router._handoff(session.userdata, "accident", pending_user_message=pending)

        self.assertEqual(session.userdata.case_type, "accident")
        self.assertEqual(len(session.userdata.handoffs), 1)
        self.assertGreaterEqual(session.userdata.handoffs[0]["at"], started)
        self.assertIsInstance(session.agent, AGENTS_BY_CASE_TYPE["accident"])


if __name__ == "__main__":
    unittest.main()
