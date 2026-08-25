"""The router - LiveKit equivalent of Retell conversation_flow_87ebb53291b2.

Retell's flow was: greeting -> extract case_type -> agent_swap (or clarify /
polite decline). Same shape here, with `session.update_agent` standing in for
`agent_swap` and no transfer_* tool calls.

Two fixes over the first port:

* Retell's extract node was a **model** reading the whole conversation, not a
  word list. `routing.classify_case_type_llm` restores that; the keyword pass
  stays in front of it so unambiguous openers route with no round-trip.
* The old code declined any matter it could not pattern-match after two turns.
  Retell only declined once clarification had *confirmed* the matter was out of
  scope. An unmatched caller now gets another clarifying question instead of a
  hang-up.
"""

from __future__ import annotations

import asyncio
import logging
import time

from livekit.agents import Agent, StopResponse, llm

from .. import models, prompts, settings
from ..capture import utterance_text
from ..routing import classify_case_type, classify_case_type_llm
from ..state import CallState
from .accident import AccidentAgent
from .base import finish_session, flatten_transcript
from .employment import EmploymentAgent
from .harassment import HarassmentAgent
from .malpractice import MalpracticeAgent
from .premises import PremisesAgent

logger = logging.getLogger("bushbush.router")

RETELL_AGENT_NAME = "Bush & Bush Law Group - Router"

AGENTS_BY_CASE_TYPE = {
    "accident": AccidentAgent,
    "employment": EmploymentAgent,
    "premises": PremisesAgent,
    "harassment": HarassmentAgent,
    "malpractice": MalpracticeAgent,
}

_DECLINE_FAREWELL = (
    "Thank you for calling Bush and Bush Law Group. We mainly help with "
    "personal injury, employment, and workplace matters, so this may not be "
    "the best fit. Please check with your local bar association for a referral. "
    "Take care."
)


class RouterAgent(Agent):
    """Speech only. `tools=[]` is the whole story — there is no `llm_node`
    override forcing `tool_choice="none"` any more.

    OpenAI rejects both `tool_choice` and `parallel_tool_calls` on a request
    that carries no `tools`, and LiveKit forwards whatever it is given without
    checking. Passing `tool_choice="none"` alongside an empty tool list was
    therefore asking for a 400 on every single router reply. An empty tool list
    already means the model cannot call anything, so the override bought
    nothing. See the note in `src/models.py`.
    """

    def __init__(self) -> None:
        super().__init__(
            instructions=prompts.ROUTER_INSTRUCTIONS,
            tools=[],
            # Retell ran the flow on gpt-4.1-nano while the destination agents
            # ran on gpt-4.1-mini. Agent-level llm overrides the session llm.
            llm=models.build_router_llm(),
        )
        # Transcript the classifier last ran on. Deepgram often finalises one
        # utterance as two messages in quick succession ("Tomorrow, I was
        # talking." then "I need help."), which used to fire the classifier
        # twice for what is really one turn — two serial round-trips in front
        # of a single reply.
        self._last_classified = ""
        # A classification that outran its inline budget. It keeps running; its
        # verdict is collected at the top of the next turn.
        self._pending: asyncio.Task | None = None

    async def on_exit(self) -> None:
        if self._pending and not self._pending.done():
            self._pending.cancel()

    async def on_enter(self) -> None:
        state: CallState = self.session.userdata
        state.agent_name = RETELL_AGENT_NAME
        delay = settings.call.begin_message_delay_ms / 1000
        if delay > 0:
            await asyncio.sleep(delay)
        self.session.say(prompts.ROUTER_BEGIN_MESSAGE)

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        state: CallState = self.session.userdata
        if state.call_ended:
            raise StopResponse()

        state.user_turns += 1
        text = utterance_text(new_message)

        transcript = flatten_transcript(turn_ctx, text)

        # Layer 0: a classification started on an earlier turn that came back
        # after the router had already spoken. Free — it is already resolved.
        verdict = self._collect_pending()

        # Layer 1: unambiguous phrasing routes with no model round-trip.
        #
        # Run the keyword pass over everything the caller has said, not just the
        # latest utterance. "I slipped" / "at the store" arriving as two short
        # turns matched nothing before and paid for an LLM round-trip; together
        # they match instantly.
        case = classify_case_type(text) or classify_case_type(
            self._user_lines(transcript)
        )

        # Layer 2: Retell's extract node. Reads the whole conversation, so a
        # caller whose first turn was "Hello?" and whose second was "my boss
        # stopped paying me" still routes on the second turn.
        #
        # Skipped for turns too short to carry a matter ("Hello?", "yes",
        # "sorry?"), and skipped when this exact transcript was already
        # classified — both only add delay.
        if (
            case is None
            and verdict is None
            and len(text.split()) >= 3
            and transcript != self._last_classified
        ):
            self._last_classified = transcript
            verdict = await self._classify_within_budget(transcript)

        if verdict is not None:
            if verdict in AGENTS_BY_CASE_TYPE:
                case = verdict
            elif verdict == "other" and state.router_asked_clarify:
                # Retell's polite-decline node: only reachable after a clarifying
                # question has already been asked and answered.
                state.case_type = "other"
                logger.info("declining out-of-scope matter after clarification")
                await finish_session(
                    self.session, state, _DECLINE_FAREWELL, "out_of_scope"
                )
                raise StopResponse()

        if case and case in AGENTS_BY_CASE_TYPE:
            self._handoff(state, case, chat_ctx=self.chat_ctx)
            raise StopResponse()

        # Still unplaced: ask one more clarifying question. Retell never hung up
        # on a caller who wanted a lawyer just because the matter was fuzzy.
        state.router_asked_clarify = True

    async def _classify_within_budget(self, transcript: str):
        """Ask the classifier, but cap how long the caller waits on it.

        The request itself gets ``classify_timeout_ms``. The *caller* only waits
        ``classify_inline_budget_ms``. If the answer misses that window the
        router speaks its clarifying question straight away and the task is
        parked on ``self._pending``, where the next turn collects it for free —
        so a slow uplink costs one extra question, never dead air.
        """
        task = asyncio.create_task(
            classify_case_type_llm(
                transcript, timeout_s=settings.llm.classify_timeout_ms / 1000
            )
        )
        done, _ = await asyncio.wait(
            {task}, timeout=settings.llm.classify_inline_budget_ms / 1000
        )
        if task in done:
            try:
                return task.result()
            except Exception:  # pragma: no cover - classify_* swallows its own
                logger.debug("router classification task failed", exc_info=True)
                return None

        logger.debug("classification over inline budget; answering without it")
        if self._pending and not self._pending.done():
            self._pending.cancel()
        self._pending = task
        return None

    def _collect_pending(self):
        """Verdict from a task parked by a previous turn, if it has landed."""
        task, self._pending = self._pending, None
        if task is None:
            return None
        if not task.done():
            # Still in flight — put it back rather than cancelling; it may well
            # answer before the caller finishes their next sentence.
            self._pending = task
            return None
        try:
            return task.result()
        except Exception:  # pragma: no cover
            return None

    @staticmethod
    def _user_lines(transcript: str) -> str:
        """Just the caller's words, so the keyword pass never matches on
        Claire's own clarifying question ("...is this about a car accident...")."""
        return " ".join(
            line[len("User: ") :]
            for line in transcript.splitlines()
            if line.startswith("User: ")
        )

    def _handoff(
        self, state: CallState, case_type: str, *, chat_ctx: llm.ChatContext
    ) -> None:
        state.case_type = case_type
        state.handoffs.append(
            {"to": case_type, "at": int(time.time() * 1000)}
        )
        logger.info("routing call to %s (no transfer tool)", case_type)
        agent_cls = AGENTS_BY_CASE_TYPE[case_type]
        self.session.update_agent(agent_cls(chat_ctx=chat_ctx, greet=False))
