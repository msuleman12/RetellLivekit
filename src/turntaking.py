"""Deciding when the caller has actually finished speaking.

Two independent mechanisms, because neither alone was enough.

**The turn detector** (see `models.build_turn_handling`) is the real fix: a
small language model that reads the transcript and predicts whether the person
is done. It is what stops "So my name is" from being treated as a finished turn.

**This module** is the belt-and-braces layer underneath it. Speech-to-text still
occasionally cuts an utterance at a pause, and when it does the agent used to
answer the fragment:

    caller: "I was"                      -> agent replies
    caller: "someone hit my car or"      -> agent replies
    caller: "intersection point and run" -> agent replies
    caller: "the side."                  -> agent replies

Four replies queued for one sentence is what produced
"failed to generate LLM completion: Request timed out" in the logs.

`FragmentBuffer` holds a fragment back instead, merges it into whatever the
caller says next, and answers the whole sentence once. If they never continue, a
grace timer answers what it has, so holding a fragment can never mean dead air.
"""

from __future__ import annotations

import asyncio
import logging
import re

logger = logging.getLogger("bushbush.turntaking")

#: Words that cannot end a finished English sentence. If the transcript stops on
#: one of these, the speaker was still going.
_DANGLING = frozenset(
    """
    a an the and but or nor so because if when while whether that which who whom
    whose than as at by for from in into of off on onto out over to under up with
    within without about after before during since until
    i we he she it they you my our his her its their your this these those there
    is are was were am be been being do does did doing have has had having
    will would shall should can could may might must
    like just really very quite also then plus versus
    """.split()
)

#: Short utterances that ARE complete answers - never hold these.
_COMPLETE_SHORT = frozenset(
    """
    yes yeah yep yup no nope nah ok okay sure right correct exactly
    hello hi hey thanks goodbye bye maybe none nothing never always
    """.split()
)

#: Spoken digits. Speech-to-text returns "214 555" when it is confident and
#: "two one four five five five" when it is not, and a half-read phone number
#: has to be held either way.
_NUMBER_WORDS = frozenset(
    """
    zero oh one two three four five six seven eight nine ten
    eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen
    twenty thirty forty fifty sixty seventy eighty ninety hundred double triple
    """.split()
)

_TERMINAL = ".!?"
_WORD_RE = re.compile(r"[A-Za-z']+")


def looks_unfinished(text: str) -> bool:
    """True when the transcript reads like the caller was cut off mid-thought.

    Deliberately conservative: a false positive costs one grace period, a false
    negative costs the caller an interruption.
    """
    raw = (text or "").strip()
    if not raw:
        return False

    words = _WORD_RE.findall(raw.lower())

    # Speech-to-text punctuates what it believes is a complete thought, so a
    # terminal mark is the strongest single signal that the caller stopped.
    if raw[-1] in _TERMINAL:
        return False

    # Digits with no terminal mark - a phone number or date still being read
    # out. "two one four" then "five five five oh one nine nine" should be one
    # number, not two turns.
    if sum(ch.isdigit() for ch in raw) >= 3:
        return True

    if not words:
        return False

    # Mostly spoken digits and no terminal mark - the number is still coming.
    spoken_digits = sum(1 for w in words if w in _NUMBER_WORDS)
    if spoken_digits >= 2 and spoken_digits >= 0.6 * len(words):
        return True

    if words[-1] in _DANGLING:
        return True

    # One or two bare words with no punctuation: a lead-in ("go ahead", "it's"),
    # unless it is one of the short answers that genuinely stand alone.
    if len(words) <= 2 and not (set(words) & _COMPLETE_SHORT):
        return True

    return False


class FragmentBuffer:
    """Holds unfinished utterances and merges them into the next one.

    One instance per agent. Not shared, not thread-safe - it lives entirely on
    the session's event loop.
    """

    def __init__(self, *, grace_s: float, max_holds: int = 3) -> None:
        self._grace_s = grace_s
        self._max_holds = max_holds
        self._parts: list[str] = []
        self._holds = 0
        self._timer: asyncio.Task | None = None
        #: Bumped on every state change. The grace timer captures it when armed
        #: and abandons the flush if it moved, which is what stops a timer that
        #: fired at the same moment as a new turn from replying twice.
        self._gen = 0

    # -- called from on_user_turn_completed ---------------------------------
    def take(self, text: str) -> str:
        """Merge anything held with `text`, and stand down the grace timer."""
        self._gen += 1
        self._cancel_timer()
        if not self._parts:
            return (text or "").strip()
        merged = " ".join([*self._parts, (text or "").strip()]).strip()
        return merged

    def should_hold(self, merged: str) -> bool:
        if self._holds >= self._max_holds:
            logger.debug("fragment held %d times already; answering it", self._holds)
            return False
        return looks_unfinished(merged)

    def hold(self, session, merged: str) -> None:
        """Keep `merged` back and wait `grace_s` for the caller to continue."""
        self._gen += 1
        gen = self._gen
        self._parts = [merged]
        self._holds += 1
        self._cancel_timer()
        self._timer = asyncio.create_task(self._flush_later(session, gen))
        logger.debug("holding unfinished utterance (%d): %r", self._holds, merged)

    def release(self) -> None:
        """The turn was answered - forget everything."""
        self._gen += 1
        self._cancel_timer()
        self._parts.clear()
        self._holds = 0

    async def aclose(self) -> None:
        self.release()

    # -- internals -----------------------------------------------------------
    def _cancel_timer(self) -> None:
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = None

    async def _flush_later(self, session, gen: int) -> None:
        try:
            await asyncio.sleep(self._grace_s)
        except asyncio.CancelledError:
            return

        # A new turn arrived while we slept, or the buffer was released.
        if gen != self._gen:
            return

        text = " ".join(self._parts).strip()
        self._parts.clear()
        self._holds = 0
        self._timer = None
        if not text:
            return

        try:
            if session.agent_state == "speaking":
                return  # never talk over the caller's own agent
            state = session.userdata
            if getattr(state, "call_ended", False):
                return
        except Exception:  # pragma: no cover - session torn down mid-flush
            return

        logger.info("caller paused mid-sentence; answering %r", text)
        try:
            session.generate_reply(user_input=text)
        except Exception:  # pragma: no cover
            logger.debug("could not flush held utterance", exc_info=True)
