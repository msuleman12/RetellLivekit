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
from ..turntaking import FragmentBuffer
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
        # One sentence, one routing decision. See src/turntaking.py.
        self._fragments = FragmentBuffer(
            grace_s=settings.call.unfinished_grace_ms / 1000
        )
        # The handoff happens from two places - the turn itself, and a late
        # classification landing between turns. Both go through this lock, and
        # `_routed` makes the second one a no-op, so the caller can never be
        # handed to two agents.
        self._route_lock = asyncio.Lock()
        self._routed = False

    async def on_exit(self) -> None:
        if self._pending and not self._pending.done():
            self._pending.cancel()
        await self._fragments.aclose()

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

        # Do not route on half a sentence. "I was" and "So my name is" were
        # each classified and answered on their own; now they are merged into
        # the next utterance and classified once.
        merged = self._fragments.take(utterance_text(new_message))
        if self._fragments.should_hold(merged):
            self._fragments.hold(self.session, merged)
            raise StopResponse()
        self._fragments.release()

        state.user_turns += 1
        text = merged
        new_message.content = [merged]

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
            # `new_message` is this turn, and it is not in any chat context
            # yet - see `_seed_chat_ctx`. Hand it to the routing path so the
            # agent taking over starts with it.
            if await self._route(state, case, pending_user_message=new_message):
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
        # Do not make the caller wait for it, but do not throw it away either:
        # act on it the moment it lands, if the router is idle and has not
        # already routed. Anything that arrives while the agent is speaking is
        # picked up by `_collect_pending` on the next turn instead.
        task.add_done_callback(self._on_late_verdict)
        return None

    def _on_late_verdict(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        asyncio.create_task(self._apply_late_verdict(task))

    async def _apply_late_verdict(self, task: asyncio.Task) -> None:
        try:
            verdict = task.result()
        except Exception:  # pragma: no cover - classify_* swallows its own
            return
        if verdict not in AGENTS_BY_CASE_TYPE:
            return
        try:
            session = self.session
            if session.agent_state != "listening":
                return  # mid-reply; the next turn will collect it
            state: CallState = session.userdata
        except Exception:  # pragma: no cover - agent no longer active
            return
        if state.call_ended:
            return
        if self._pending is task:
            self._pending = None
        logger.info("late classification landed; routing to %s", verdict)
        await self._route(state, verdict)

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

    async def _route(
        self,
        state: CallState,
        case_type: str,
        *,
        pending_user_message: llm.ChatMessage | None = None,
    ) -> bool:
        """Hand off exactly once. Returns True if this call did the handoff."""
        async with self._route_lock:
            if self._routed:
                return False
            self._routed = True
            self._handoff(state, case_type, pending_user_message=pending_user_message)
            return True

    def _seed_chat_ctx(self, pending: llm.ChatMessage | None) -> llm.ChatContext:
        """The history the new agent starts from, including the turn in flight.

        `Agent.chat_ctx` does not contain the message currently being handled.
        LiveKit commits a user turn as part of generating the reply to it, and
        this path raises `StopResponse` instead, so no reply is generated and
        the turn is never committed anywhere - not to the router's context and
        not to `session.history`.

        That matters more here than anywhere else in the call. The utterance
        that triggers routing is, almost by definition, the one where the
        caller says what happened - routing is what recognising the matter
        *is*. Losing it meant the intake agent opened by asking about the
        thing it had just been told, and the firm's transcript never contained
        the caller's own account of the incident.

        Seeding the copy is free: an in-memory append, no model call, no
        network. It costs one utterance of context per call and saves the
        whole redundant turn that used to follow.
        """
        ctx = self.chat_ctx.copy()
        if pending is not None and ctx.index_by_id(pending.id) is None:
            ctx.items.append(pending)
        return ctx

    def _commit_to_history(self, pending: llm.ChatMessage | None) -> None:
        """Put the same turn into `session.history`.

        The session keeps its own transcript, fed by LiveKit as items are
        added; seeding the new agent's context does not reach it. Without
        this the model would remember the turn while `postcall.build_transcript`
        - and so the record the firm receives - still would not.
        """
        if pending is None:
            return
        try:
            history = self.session.history
            if history.index_by_id(pending.id) is not None:
                return
            self.session._conversation_item_added(pending)
        except Exception:  # pragma: no cover - private API, never worth a call
            logger.warning(
                "could not add the routing turn to session history; the model "
                "still has it, the post-call transcript will not",
                exc_info=True,
            )

    def _handoff(
        self,
        state: CallState,
        case_type: str,
        *,
        pending_user_message: llm.ChatMessage | None = None,
    ) -> None:
        state.case_type = case_type
        state.handoffs.append(
            {"to": case_type, "at": int(time.time() * 1000)}
        )
        logger.info("routing call to %s (no transfer tool)", case_type)
        chat_ctx = self._seed_chat_ctx(pending_user_message)
        self._commit_to_history(pending_user_message)
        agent_cls = AGENTS_BY_CASE_TYPE[case_type]
        self.session.update_agent(agent_cls(chat_ctx=chat_ctx, greet=False))
