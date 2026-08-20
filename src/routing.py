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

import json
import logging
import re
from typing import Literal

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
    r"malpractice|medical\s+negligen|botched\s+(?:surgery|procedure)|"
    r"misdiagnos|surgical\s+error|doctor\s+(?:erred|error|negligen)|"
    r"hospital\s+(?:negligen|error)",
    re.IGNORECASE,
)
_EMPLOYMENT = re.compile(
    r"\b(?:workplace|employer|employment|wrongful\s+terminat|fired|terminated|"
    r"wages?|discrimination|retaliation|hurt\s+at\s+work|injured\s+at\s+work|"
    r"on\s+the\s+job|at\s+my\s+job|workers?\s+comp)\b",
    re.IGNORECASE,
)
_ACCIDENT = re.compile(
    r"\b(?:car\s+accident|auto\s+accident|vehicle|crash(?:ed)?|hit[\s-]?and[\s-]?run|"
    r"rear[\s-]?end|collision|fender\s+bender|motor\s+vehicle|"
    r"call\s+accident|call\s+ex)\b|"
    r"\b(?:hit|struck)\s+(?:my|our|the)\s+car\b",
    re.IGNORECASE,
)
_PREMISES = re.compile(
    r"\b(?:slip(?:ped)?\s+and\s+fell?\b|trip(?:ped)?\s+and\s+fell?\b|"
    r"slip[\s-]?fall|premises|"
    r"fell\s+(?:at|in|on)\s+(?:a\s+)?(?:store|walmart|target|parking|"
    r"sidewalk|grocery|restaurant|mall))\b",
    re.IGNORECASE,
)


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

_CLASSIFY_SYSTEM = """You route a caller to the right legal intake specialist.

Read everything said so far and return ONE category.

{rules}

Return "unclear" — never "other" — when the caller wants a lawyer but has not
said enough to place the matter yet. "other" is only correct once they have
clearly confirmed the matter is none of the five practice areas.
"""


async def classify_case_type_llm(transcript: str, *, timeout_s: float = 2.5):
    """Retell's extract node. Returns a case type, "unclear", or None on error.

    Errors and timeouts return None so the caller falls back to asking one
    clarifying question — the same thing Retell's else-edge did.
    """
    if not transcript.strip():
        return None

    from . import prompts, settings

    try:
        import asyncio

        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.llm.api_key or None)
        completion = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.llm.router_model,
                temperature=0,
                response_format={"type": "json_schema", "json_schema": _CLASSIFY_SCHEMA},
                messages=[
                    {
                        "role": "system",
                        "content": _CLASSIFY_SYSTEM.format(
                            rules=prompts.ROUTER_CASE_TYPE_RULES
                        ),
                    },
                    {"role": "user", "content": transcript},
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
        logger.warning("router classification unavailable (%s); clarifying instead", exc)
        return None
