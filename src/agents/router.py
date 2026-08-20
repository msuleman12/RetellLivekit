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
from .base import finish_session
from .employment import EmploymentAgent
from .harassment import HarassmentAgent
from .malpractice import MalpracticeAgent
from .premises import PremisesAgent

logger = logging.getLogger("bushbush.router")

RETELL_AGENT_NAME = "Bush & Bush Law Group - Router"

_AGENTS = {
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

        # Layer 1: unambiguous phrasing routes with no model round-trip.
        case = classify_case_type(text)

        # Layer 2: Retell's extract node. Reads the whole conversation, so a
        # caller whose first turn was "Hello?" and whose second was "my boss
        # stopped paying me" still routes on the second turn.
        #
        # Skipped for turns too short to carry a matter ("Hello?", "yes",
        # "sorry?"), which are the ones where a model round-trip would only add
        # delay before an unavoidable clarifying question.
        if case is None and len(text.split()) >= 3:
            transcript = self._transcript(turn_ctx, text)
            verdict = await classify_case_type_llm(
                transcript, timeout_s=settings.llm.classify_timeout_ms / 1000
            )
            if verdict in _AGENTS:
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

        if case and case in _AGENTS:
            self._handoff(state, case, chat_ctx=self.chat_ctx)
            raise StopResponse()

        # Still unplaced: ask one more clarifying question. Retell never hung up
        # on a caller who wanted a lawyer just because the matter was fuzzy.
        state.router_asked_clarify = True

    @staticmethod
    def _transcript(turn_ctx: llm.ChatContext, latest_user_text: str = "") -> str:
        """Flatten the conversation for the classifier.

        `latest_user_text` is passed in because the turn that just finished is
        not necessarily in `turn_ctx` yet — and it is the one that decides the
        routing, so leaving it out would classify the conversation as it stood
        one turn ago.
        """
        lines: list[str] = []
        for item in turn_ctx.items:
            role = getattr(item, "role", None)
            if role not in ("user", "assistant"):
                continue
            text = (getattr(item, "text_content", None) or "").strip()
            if text:
                lines.append(f"{'Agent' if role == 'assistant' else 'User'}: {text}")
        latest = (latest_user_text or "").strip()
        if latest and not lines[-1:] == [f"User: {latest}"]:
            lines.append(f"User: {latest}")
        return "\n".join(lines)

    def _handoff(
        self, state: CallState, case_type: str, *, chat_ctx: llm.ChatContext
    ) -> None:
        state.case_type = case_type
        state.handoffs.append(
            {"to": case_type, "at": int(time.time() * 1000)}
        )
        logger.info("routing call to %s (no transfer tool)", case_type)
        agent_cls = _AGENTS[case_type]
        self.session.update_agent(agent_cls(chat_ctx=chat_ctx, greet=False))
