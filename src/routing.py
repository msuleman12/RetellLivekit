"""Case-type routing — Retell's `extract-case-type` node, in two layers.

Retell's router was a conversation flow whose `extract_dynamic_variables` node
asked gpt-4.1-nano to pick one of six enum values, using the long description
now quoted in ``prompts.ROUTER_CASE_TYPE_RULES``. A model, not a word list.

The first LiveKit port replaced that with regular expressions, which is why
"my boss won't pay me" or "I got hurt in a store" fell through to a clarifying
question the caller had already answered.

Both layers live here:

  * :func:`classify_case_type` — the keyword pass. Unambiguous phrasings route
    instantly with no model round-trip, which keeps the common case fast.
  * :func:`classify_case_type_llm` — Retell's actual node. Runs whenever the
    keyword pass is unsure, with the enum description copied verbatim.

Neither ever returns ``other`` on its own from the keyword pass: Retell's rule
was "Never decline on first unclear utterance — clarify first."
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Literal

from . import prompts, settings

logger = logging.getLogger("bushbush.routing")

CaseType = Literal[
    "accident", "employment", "premises", "harassment", "malpractice", "other"
]

_WORD = r"(?:^|[^a-z])"

_HARASSMENT = re.compile(
    r"sexual\s+harass|sexually\s+harass|sexual\s+assault|molested|"
    r"inappropriate\s+touch|harass(?:ed|ment)?\s+(?:at\s+)?(?:work|job)",
    re.IGNORECASE,
)
_MALPRACTICE = re.compile(
    # "botched my surgery" / "botched the knee procedure" used to fall through
    # because the noun had to follow "botched" immediately.
    r"malpractice|medical\s+negligen|"
    r"botched\s+(?:\w+\s+){0,2}(?:surgery|procedure|operation)|"
    r"misdiagnos|surgical\s+error|doctor\s+(?:erred|error|negligen)|"
    r"hospital\s+(?:negligen|error)",
    re.IGNORECASE,
)
_EMPLOYMENT = re.compile(
    r"\b(?:workplace|employer|employment|wrongful\s+terminat|fired|terminated|"
    r"wages?|discrimination|retaliation|hurt\s+at\s+work|injured\s+at\s+work|"
    # Bare "at work" was missing, so "I had an accident at work" fell through to
    # the accident branch — the exact misroute Retell's rules warn about
    # ("Injury while working for employer -> employment NOT accident").
    r"at\s+work|on\s+the\s+job|at\s+my\s+job|workers?\s+comp)\b",
    re.IGNORECASE,
)
_ACCIDENT = re.compile(
    r"\b(?:car\s+accident|auto\s+accident|vehicle|crash(?:ed)?|hit[\s-]?and[\s-]?run|"
    # `rear[\s-]?end` alone never matched "rear-ended" — the trailing \b on the
    # group landed inside the word.
    r"rear[\s-]?end(?:ed)?|collision|fender\s+bender|motor\s+vehicle|"
    r"call\s+accident|call\s+ex)\b|"
    r"\b(?:hit|struck)\s+(?:my|our|the)\s+car\b",
    re.IGNORECASE,
)
_PREMISES = re.compile(
    r"\b(?:slip(?:ped)?\s+and\s+fell?\b|trip(?:ped)?\s+and\s+fell?\b|"
    r"slip[\s-]?fall|premises|"
    # "I slipped" and "at the store" often arrive as two short turns; joined,
    # they should match here rather than costing a model round-trip. Employment
    # is tested first, so "slipped at work" still routes to employment.
    r"(?:slip(?:ped)?|trip(?:ped)?|fell|fall)\s+(?:at|in|on|down|inside|outside)\b|"
    r"fell\s+(?:at|in|on)\s+(?:a\s+)?(?:store|walmart|target|parking|"
    r"sidewalk|grocery|restaurant|mall))\b",
    re.IGNORECASE,
)

#: Last resort, and only after every other area has been ruled out. Deepgram
#: mangles "car accident" constantly — the logs alone have produced "call
#: accident", "call ex" and "quad accident". Enumerating mishears is a losing
#: game; for a personal-injury firm, a bare "accident" that is not a workplace,
#: medical, harassment or premises matter is an accident.
_ACCIDENT_LOOSE = re.compile(r"\baccident\b|\bwreck\b", re.IGNORECASE)


def classify_case_type(text: str) -> CaseType | None:
    """Return a practice-area label, or None if the utterance is still unclear.

    Priority matches Retell: harassment / malpractice / employment before
    premises; accident for car/crash; never returns ``other`` here — the
    router declines only after a clarify turn still fails to match.
    """
    if not text or not text.strip():
        return None
    raw = text.strip()

    if _HARASSMENT.search(raw):
        return "harassment"
    if _MALPRACTICE.search(raw):
        return "malpractice"
    # Employment before premises: injury while working for employer.
    if _EMPLOYMENT.search(raw):
        return "employment"
    if _ACCIDENT.search(raw):
        return "accident"
    if _PREMISES.search(raw):
        return "premises"
    # Checked last, after premises, so "I had an accident, slipped in the store"
    # still routes to premises.
    if _ACCIDENT_LOOSE.search(raw):
        return "accident"
    return None


# ---------------------------------------------------------------------------
# Retell conversation_flow_87ebb53291b2, node `extract-case-type`
# ---------------------------------------------------------------------------
_CLASSIFY_SCHEMA = {
    "name": "bushbush_case_type",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "case_type": {
                "type": "string",
                "enum": [
                    "accident",
                    "employment",
                    "premises",
                    "harassment",
                    "malpractice",
                    "other",
                    "unclear",
                ],
            },
        },
        "required": ["case_type"],
        "additionalProperties": False,
    },
}



# ---------------------------------------------------------------------------
# One shared OpenAI client for the whole process.
#
# This used to build `AsyncOpenAI(...)` inside the function, i.e. once per
# caller turn. Every construction brings its own httpx client and therefore its
# own DNS lookup + TCP + TLS handshake to api.openai.com before a single token
# moves — several hundred ms of dead air on every turn. A module-level client
# keeps the connection pool warm across turns.
# ---------------------------------------------------------------------------
_client: Any = None


def _get_client() -> Any:
    global _client
    if _client is None:
        from .models import build_async_openai

        _client = build_async_openai(max_retries=0)
    return _client


#: The classifier only ever needs the recent conversation. Sending the whole
#: transcript grows the prompt (and the time-to-first-token) linearly with call
#: length for no gain in routing accuracy.
_MAX_TRANSCRIPT_LINES = 10
_MAX_TRANSCRIPT_CHARS = 1500


def trim_transcript(transcript: str) -> str:
    lines = [ln for ln in transcript.splitlines() if ln.strip()][-_MAX_TRANSCRIPT_LINES:]
    text = "\n".join(lines)
    return text[-_MAX_TRANSCRIPT_CHARS:]


async def classify_case_type_llm(
    transcript: str, *, timeout_s: float = 2.5
) -> str | None:
    """Retell's extract node. Returns a case type, "unclear", or None on error.

    Errors and timeouts return None so the caller falls back to asking one
    clarifying question — the same thing Retell's else-edge did.
    """
    if not transcript.strip():
        return None

    try:
        client = _get_client()
        completion = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.llm.router_model,
                temperature=0,
                # The reply is a single enum value; without a cap the model is
                # free to spend time it does not need.
                max_tokens=16,
                response_format={"type": "json_schema", "json_schema": _CLASSIFY_SCHEMA},
                messages=[
                    {
                        "role": "system",
                        "content": prompts.ROUTER_CLASSIFY_SYSTEM.format(
                            rules=prompts.ROUTER_CASE_TYPE_RULES
                        ),
                    },
                    {"role": "user", "content": trim_transcript(transcript)},
                ],
            ),
            timeout=timeout_s,
        )
        value = json.loads(completion.choices[0].message.content or "{}").get(
            "case_type"
        )
        logger.info("router classified matter as %r", value)
        return value or None
    except Exception as exc:
        # `str(TimeoutError())` is the empty string, which produced the useless
        # "router classification unavailable ()" line in the logs. Name the type
        # so a timeout is distinguishable from an auth or network failure.
        logger.warning(
            "router classification unavailable (%s: %s); clarifying instead",
            type(exc).__name__,
            exc or "no detail",
        )
        return None
