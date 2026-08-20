"""Live, model-driven capture of intake fields — the LiveKit stand-in for the
part of Retell you could not see.

Retell never asked the *conversation* model to fill a form. It let Claire talk,
and a second model read the transcript afterwards to populate
`post_call_analysis_data`. That is why Retell calls sounded conversational: the
speaking model was never carrying a checklist.

The first LiveKit port tried to reproduce that with regular expressions in
`capture.py`. Regexes only recognise the phrasings someone thought of. A caller
who answers "Yes. Moss Ali." — no "my name is" — was never captured, so Claire
asked for the name again, and again, and the call turned into an interrogation.

So this module does what Retell did, only during the call instead of after it:
a cheap model reads the transcript after every caller turn and returns the same
field names Retell's schema uses. It runs in the background, so it never adds
latency to Claire's reply; its results land in `CallState` in time for the next
turn. `capture.py` stays as an instant fast-path for phone digits, which are
deterministic and worth having before the model comes back.

Nothing in here decides what Claire says or when the call ends. It only records.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from . import settings
from .schemas import FIELDS_BY_CASE_TYPE, AnalysisField
from .state import CallState, is_valid_us_number

logger = logging.getLogger("bushbush.extract")

#: Handled explicitly as must-haves; excluded from the optional-field sweep.
_MUST_HAVE_NAMES = frozenset(
    {"user_fname", "user_lname", "user_phone", "other_party_name"}
)

SYSTEM_PROMPT = """You are a silent note-taker listening to a live legal intake call.

You never speak to the caller and you never decide what happens next. You only
record what has ALREADY been said, out loud, in the transcript below.

Rules:
- Record only what the caller actually said. Never infer, never complete a
  half-given answer, never fill a gap with something plausible.
- If something has not been said yet, or you are not confident you heard it
  right, return null. A null is always better than a guess.
- Names: only when the caller gave them as their own name. "Moss Ali" answered
  to "what's your name" counts. A name they mention in the story does not.
- Phone: digits only, exactly the ten US digits the caller spoke. If they gave a
  country code, drop it. If fewer than ten real digits were spoken, return null.
- other_party_name is the OTHER side - the person, employer, property, or
  provider the caller is in dispute with. Never the caller. Never the law firm.
  If the caller clearly said they do not know, return exactly "I don't know".
- Do not assess the case, do not give opinions, do not summarise the agent.
"""


def _optional_fields(case_type: str) -> tuple[AnalysisField, ...]:
    fields = FIELDS_BY_CASE_TYPE.get(case_type) or FIELDS_BY_CASE_TYPE["accident"]
    return tuple(f for f in fields if f.name not in _MUST_HAVE_NAMES)


def _live_schema(case_type: str) -> dict[str, Any]:
    """Must-haves + closing signals + this practice area's optional fields."""
    props: dict[str, Any] = {
        "user_fname": {
            "type": ["string", "null"],
            "description": "The caller's own first name, as they gave it. Null if not given yet.",
        },
        "user_lname": {
            "type": ["string", "null"],
            "description": "The caller's own last name, as they gave it. Null if not given yet.",
        },
        "user_phone": {
            "type": ["string", "null"],
            "description": (
                "The callback number the caller spoke aloud, digits only, exactly "
                "10 US digits with any country code removed. Null if they have "
                "not said a full 10-digit number."
            ),
        },
        "phone_read_back_confirmed": {
            "type": ["boolean", "null"],
            "description": (
                "True only if the agent read a number back AND the caller then "
                "confirmed it was right. False if the caller corrected it. Null "
                "if no read-back has happened."
            ),
        },
        "other_party_name": {
            "type": ["string", "null"],
            "description": (
                "The other side's name - person, employer, business, property or "
                "medical provider. Exactly \"I don't know\" if the caller said so. "
                "Never the caller's own name, never Bush and Bush."
            ),
        },
        "incident_summary": {
            "type": ["string", "null"],
            "description": (
                "One or two plain sentences on what happened, when and where, in "
                "the caller's own terms. Null until they have described it."
            ),
        },
        "agent_offered_the_close": {
            "type": ["boolean", "null"],
            "description": (
                "True if the agent has already told the caller an attorney will "
                "review it and someone will call back, AND asked whether they "
                "have any questions or anything else."
            ),
        },
        "caller_said_they_are_finished": {
            "type": ["boolean", "null"],
            "description": (
                "True ONLY if, after the agent offered the close, the caller "
                "clearly said they have nothing else and no questions, or said "
                "goodbye. False while they are still talking, still asking, or "
                "still mid-story. When in doubt, false."
            ),
        },
        "topics_already_raised": {
            "type": ["array", "null"],
            "items": {"type": "string"},
            "description": (
                "Field names from the list below that the AGENT has already asked "
                "about, whether or not the caller answered. Used so the agent "
                "does not ask the same thing twice."
            ),
        },
    }

    for f in _optional_fields(case_type):
        if f.type == "boolean":
            inner: dict[str, Any] = {"type": ["boolean", "null"]}
        elif f.type == "number":
            inner = {"type": ["number", "null"]}
        else:
            inner = {"type": ["string", "null"]}
        description = f.description
        if f.choices:
            description += f" Must be one of: {', '.join(f.choices)}."
        if f.conditional_prompt:
            description += f" {f.conditional_prompt}"
        inner["description"] = description
        props[f.name] = inner

    return {
        "name": f"bushbush_live_{case_type or 'accident'}",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": props,
            "required": list(props.keys()),
            "additionalProperties": False,
        },
    }


class LiveExtractor:
    """One per call. Serialises its own runs so turns cannot race each other."""

    def __init__(self, case_type: str) -> None:
        self.case_type = case_type
        self._lock = asyncio.Lock()
        self._client: Any = None
        self._schema = _live_schema(case_type)
        self._optional_names = tuple(f.name for f in _optional_fields(case_type))
        self._consecutive_failures = 0
        self._disabled = False

    #: A bad API key fails every time and should not log once per turn for the
    #: rest of the call; a dropped connection should not cost the caller the
    #: model-driven capture path for the whole call either. Three strikes.
    MAX_CONSECUTIVE_FAILURES = 3

    # ------------------------------------------------------------------
    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=settings.llm.api_key or None)
        return self._client

    async def run(self, state: CallState, transcript: str) -> list[str]:
        """Read the transcript, update `state`, return notes on what changed."""
        if self._disabled or not transcript.strip() or state.call_ended:
            return []

        async with self._lock:
            try:
                raw = await asyncio.wait_for(
                    self._call_model(transcript),
                    timeout=settings.llm.live_extract_timeout_ms / 1000,
                )
            except asyncio.TimeoutError:
                logger.debug("live extraction timed out; regex fast-path still applies")
                return []
            except Exception as exc:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    # Almost certainly a bad key or model name. Stop retrying so
                    # the log does not fill up for the rest of the call; capture
                    # falls back to `capture.py`, which still covers the phone
                    # number, the read-back, names and the conflict check.
                    self._disabled = True
                    logger.warning(
                        "live extraction disabled after %d failures, regex capture "
                        "only from here: %s",
                        self._consecutive_failures,
                        exc,
                    )
                else:
                    logger.warning("live extraction failed (retrying next turn): %s", exc)
                return []

            self._consecutive_failures = 0
            return self._merge(state, raw)

    async def _call_model(self, transcript: str) -> dict[str, Any]:
        client = self._get_client()
        completion = await client.chat.completions.create(
            model=settings.llm.live_extract_model,
            temperature=0,
            response_format={"type": "json_schema", "json_schema": self._schema},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Practice area: {self.case_type or 'unknown'}\n\n"
                        f"Transcript so far:\n{transcript}"
                    ),
                },
            ],
        )
        return json.loads(completion.choices[0].message.content or "{}")

    # ------------------------------------------------------------------
    def _merge(self, state: CallState, raw: dict[str, Any]) -> list[str]:
        notes: list[str] = []

        first = (raw.get("user_fname") or "").strip()
        last = (raw.get("user_lname") or "").strip()
        if first and not state.first_name:
            state.first_name = first.title()
            notes.append(f"first_name={state.first_name}")
        if last and not state.last_name:
            state.last_name = last.title()
            notes.append(f"last_name={state.last_name}")

        phone = "".join(ch for ch in str(raw.get("user_phone") or "") if ch.isdigit())
        if len(phone) == 11 and phone.startswith("1"):
            phone = phone[1:]
        if is_valid_us_number(phone) and not state.phone:
            state.phone = phone
            notes.append(f"phone={phone}")
        if raw.get("phone_read_back_confirmed") is True and state.phone:
            if not state.phone_read_back:
                state.phone_read_back = True
                notes.append("phone_read_back=confirmed")

        other = (raw.get("other_party_name") or "").strip()
        if other and not state.other_party_name:
            lowered = other.lower()
            is_self = bool(state.full_name) and lowered == state.full_name.lower()
            is_firm = "bush and bush" in lowered or "bush & bush" in lowered
            if not is_self and not is_firm:
                state.other_party_name = other
                notes.append(f"other_party={other}")

        summary = (raw.get("incident_summary") or "").strip()
        if summary and not state.incident_summary:
            state.incident_summary = summary[:500]
            notes.append("incident=captured")

        if raw.get("agent_offered_the_close") is True and not state.closing_offered:
            state.closing_offered = True
            state.callback_promised = True
            notes.append("closing_offered=True")

        # Only meaningful once the close has actually been offered - otherwise a
        # caller who says "that's all" mid-story would unlock the hangup.
        finished = raw.get("caller_said_they_are_finished") is True
        if finished and state.closing_offered and not state.caller_done:
            state.caller_done = True
            notes.append("caller_done=True")
        elif not finished and state.caller_done:
            # They started talking again. Retell would have kept going, so do that.
            state.caller_done = False
            notes.append("caller_done=False (still talking)")

        for name in self._optional_names:
            if name in raw and state.record_optional(name, raw[name]):
                notes.append(f"{name}={raw[name]!r}")

        raised = raw.get("topics_already_raised") or []
        if isinstance(raised, list):
            for topic in raised:
                if isinstance(topic, str) and topic in self._optional_names:
                    state.asked_topics.add(topic)

        if notes:
            logger.info("live extraction: %s", ", ".join(notes))
        return notes

    # ------------------------------------------------------------------
    def still_unknown(self, state: CallState, limit: int = 6) -> tuple[str, ...]:
        """A short menu of optional topics nobody has covered.

        Deliberately unordered from the model's point of view and capped, so it
        reads as "here are some things you could ask" rather than a queue to
        work through. `CONVERSATIONAL_BLOCK` in the prompt says as much.
        """
        fields = {f.name: f for f in _optional_fields(state.case_type or self.case_type)}
        out: list[str] = []
        for name, f in fields.items():
            if name in state.optional_fields or name in state.asked_topics:
                continue
            out.append(f"{name} — {f.description}")
            if len(out) >= limit:
                break
        return tuple(out)
