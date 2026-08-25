"""Prompts copied verbatim from the Retell agents.

Nothing here is paraphrased.  `RETELL_SOURCE` on each constant records which
Retell object the text came from so you can diff them later.

Retell composed an agent's effective system prompt from three things:

    1. the Response Engine `general_prompt` (or the flow `global_prompt`),
    2. the `handbook_config` toggles, which Retell expands server-side into
       extra behavioural rules,
    3. `expressive_mode_prompt`, delivery guidance for the TTS layer.

LiveKit has no handbook or expressive layer, so the parts of (2) and (3) that
the Retell prompt does not already state are written out below and appended to
each agent's instructions - once each. See the note above `DELIVERY_BLOCK` for
why "once each" is the whole point."""

from __future__ import annotations

from .common import (
    DELIVERY_BLOCK,
    END_CALL_TOOL_DESCRIPTION,
    HANDOFF_CONTINUATION_INSTRUCTION,
    LIVE_EXTRACT_SYSTEM,
    OPERATING_BLOCK,
    POST_CALL_SYSTEM,
    SILENCE_REMINDER_INSTRUCTION,
    compose,
)
from .router import (
    ROUTER_BEGIN_MESSAGE,
    ROUTER_CASE_TYPE_RULES,
    ROUTER_CLARIFY_INSTRUCTION,
    ROUTER_CLASSIFY_SYSTEM,
    ROUTER_DECLINE_INSTRUCTION,
    ROUTER_GLOBAL_PROMPT,
    ROUTER_GREETING_INSTRUCTION,
    ROUTER_INSTRUCTIONS,
    ROUTER_NO_TOOLS_BLOCK,
    ROUTER_ROUTING_INSTRUCTION,
)
from .accident import (
    ACCIDENT_BEGIN_MESSAGE,
    ACCIDENT_PROMPT,
)
from .employment import (
    EMPLOYMENT_BEGIN_MESSAGE,
    EMPLOYMENT_PROMPT,
)
from .premises import (
    PREMISES_BEGIN_MESSAGE,
    PREMISES_PROMPT,
)
from .malpractice import (
    MALPRACTICE_BEGIN_MESSAGE,
    MALPRACTICE_PROMPT,
)
from .harassment import (
    HARASSMENT_BEGIN_MESSAGE,
    HARASSMENT_PROMPT,
)

__all__ = [
    "ACCIDENT_BEGIN_MESSAGE",
    "ACCIDENT_PROMPT",
    "DELIVERY_BLOCK",
    "EMPLOYMENT_BEGIN_MESSAGE",
    "EMPLOYMENT_PROMPT",
    "END_CALL_TOOL_DESCRIPTION",
    "HANDOFF_CONTINUATION_INSTRUCTION",
    "HARASSMENT_BEGIN_MESSAGE",
    "HARASSMENT_PROMPT",
    "LIVE_EXTRACT_SYSTEM",
    "MALPRACTICE_BEGIN_MESSAGE",
    "MALPRACTICE_PROMPT",
    "OPERATING_BLOCK",
    "POST_CALL_SYSTEM",
    "PREMISES_BEGIN_MESSAGE",
    "PREMISES_PROMPT",
    "ROUTER_BEGIN_MESSAGE",
    "ROUTER_CASE_TYPE_RULES",
    "ROUTER_CLARIFY_INSTRUCTION",
    "ROUTER_CLASSIFY_SYSTEM",
    "ROUTER_DECLINE_INSTRUCTION",
    "ROUTER_GLOBAL_PROMPT",
    "ROUTER_GREETING_INSTRUCTION",
    "ROUTER_INSTRUCTIONS",
    "ROUTER_NO_TOOLS_BLOCK",
    "ROUTER_ROUTING_INSTRUCTION",
    "SILENCE_REMINDER_INSTRUCTION",
    "compose",
]
