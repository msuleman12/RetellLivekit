"""Per-call state (LiveKit `userdata`).

Retell tracked the must-haves only inside the prompt ("FORBIDDEN - end_call").
Here they are also written via `record_*` tools into `CallState` for post-call
analysis and for per-turn ALREADY COLLECTED injection so Claire does not re-ask.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

#: How many times to ask for the callback number before letting the call move
#: on. A caller who has said it three times and is still being asked is having
#: a worse experience than the firm gets value from a fourth attempt.
MAX_PHONE_ATTEMPTS = 3

#: How many turns a single STILL UNKNOWN field may stay on the menu before it
#: steps aside for the ones behind it.
#:
#: `still_unknown` returns the first N unfilled fields in schema order, and a
#: field leaves only when it is recorded or the extractor marks it asked. The
#: top of every practice area's list is held by fields whose schema says "only
#: populate if the caller mentioned it" - email, preferred contact, best time
#: to reach. Callers rarely volunteer those, so nothing filled them, nothing
#: marked them asked, and the same names were handed to the model turn after
#: turn. Claire asked, got a vague answer or none, saw the name again next
#: turn, and asked again.
#:
#: Three turns is enough for one of them to come up naturally. After that the
#: field stands down and the ones behind it - the case-specific questions -
#: finally get a turn.
MAX_TOPIC_OFFERS = 3

_WORD_DIGITS: dict[str, str] = {
    "zero": "0",
    "oh": "0",
    "o": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}


def extract_phone_digits(raw: str | None) -> str:
    """Pull digit characters from spoken or typed phone input.

    Handles fillers like "oh"→0 and "two one four…", and collapses whitespace.
    """
    if not raw:
        return ""
    text = raw.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = text.split()
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.isdigit():
            out.append(tok)
            i += 1
            continue
        if tok in ("double", "triple") and i + 1 < len(tokens):
            nxt = tokens[i + 1]
            digit = _WORD_DIGITS.get(nxt, nxt if len(nxt) == 1 and nxt.isdigit() else "")
            if digit:
                repeats = 2 if tok == "double" else 3
                out.append(digit * repeats)
                i += 2
                continue
        if tok in _WORD_DIGITS:
            out.append(_WORD_DIGITS[tok])
            i += 1
            continue
        # Keep embedded digits inside mixed tokens ("214-555")
        digits = re.sub(r"\D", "", tok)
        if digits:
            out.append(digits)
        i += 1
    return "".join(out)


def is_valid_us_number(digits: str, *, allow_test: bool = False) -> bool:
    """NANP validity: 10 digits, area code and exchange both start 2-9.

    Retell's prompt said "ignore country code 1 if they say it" and "must be 10
    US digits".  Without this check a caller who says "+1 290-909-490" (country
    code plus only nine digits) produced the plausible-looking but impossible
    `1290909490`, and both the agent and the post-call webhook accepted it.

    Console mic tests use numbers like 123-456-7890; pass ``allow_test=True``
    (or ``ALLOW_TEST_PHONE_NUMBERS``) so those are accepted locally only.
    """
    if len(digits) != 10 or not digits.isdigit():
        return False
    if allow_test or _allow_test_numbers():
        return True
    return digits[0] in "23456789" and digits[3] in "23456789"


def _allow_test_numbers() -> bool:
    """`ALLOW_TEST_PHONE_NUMBERS=true` accepts 555/123-style fake numbers.

    Strictly for console testing. Never set it in production: it is the check
    that stops an unreachable number reaching the firm.
    """
    return os.getenv("ALLOW_TEST_PHONE_NUMBERS", "").strip().lower() in (
        "1", "true", "yes", "y", "on",
    )


def normalize_phone(raw: str | None, *, allow_test: bool = False) -> str | None:
    """Return a valid 10-digit US number, or None if we do not have one yet.

    This used to go hunting: on a digit string longer than 11 it scanned for
    ANY contiguous 10-digit window that happened to look valid and returned it.
    On a real call the caller's first, garbled attempt —

        "plus 1 2 2, 3, 45, 56, 78, and 90"  ->  122345567890

    — produced `2234556789`, a number nobody had said. It was stored, never
    revisited, and shipped to the firm, while the agent went on asking for a
    correct number four more times.

    A digit run that is not 10, or 11 starting with the country code, is a
    mis-hear. The honest answer is None: ask again rather than invent.
    """
    digits = extract_phone_digits(raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return digits if is_valid_us_number(digits, allow_test=allow_test) else None


def phone_digit_count(raw: str | None) -> int:
    """How many digits we heard (after spoken-word conversion), max useful for retries."""
    digits = extract_phone_digits(raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return len(digits)


def mask_phone(raw: str | None) -> str:
    """Last four digits only, for logs. Empty input stays empty."""
    if not raw or not str(raw).strip():
        return ""
    digits = extract_phone_digits(raw)
    if len(digits) >= 4:
        return f"***{digits[-4:]}"
    return "***"


@dataclass
class CallState:
    # --- identity of the call -------------------------------------------------
    call_id: str = ""
    room_name: str = ""
    from_number: str = ""
    to_number: str = ""
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None

    # --- routing --------------------------------------------------------------
    case_type: str = ""
    agent_name: str = "Bush & Bush Law Group - Router"
    handoffs: list[dict[str, Any]] = field(default_factory=list)
    #: how many turns the caller has taken; the router may not decline a matter
    #: on the first one (Retell: "Never decline on first unclear utterance").
    user_turns: int = 0
    #: True after the router has already asked a clarifying question.
    router_asked_clarify: bool = False

    # --- the four must-haves --------------------------------------------------
    first_name: str = ""
    last_name: str = ""
    phone: str = ""  # normalized, 10 digits
    phone_read_back: bool = False
    #: How many times the caller has tried to give a number. After
    #: MAX_PHONE_ATTEMPTS the agent stops asking — see `missing_must_haves`.
    phone_attempts: int = 0
    #: The digits from the caller's last attempt, even when they did not form a
    #: valid US number. Better the firm sees "they said 1234567890, unverified"
    #: than an empty field or, worse, a number nobody spoke.
    phone_heard_raw: str = ""
    #: True when the number on file failed validation but we stopped asking.
    phone_unverified: bool = False
    other_party_name: str = ""  # a name, or the literal "I don't know"
    incident_summary: str = ""
    # The sexual-harassment prompt explicitly says not to press for the other
    # party if the caller is distressed, so that agent relaxes this one.
    other_party_required: bool = True

    # --- optional / conversational fields ------------------------------------
    #: Everything beyond the four must-haves that the caller has already covered.
    #: Keyed by the Retell `post_call_analysis_data` field name so the live view
    #: and the post-call payload speak the same language.
    optional_fields: dict[str, Any] = field(default_factory=dict)
    #: Topics Claire has already raised, so she varies instead of repeating.
    asked_topics: set[str] = field(default_factory=set)
    #: How many turns each STILL UNKNOWN field has been offered on. See
    #: MAX_TOPIC_OFFERS - this is what stops one field being suggested for the
    #: whole call.
    topic_offers: dict[str, int] = field(default_factory=dict)
    #: The turn each field was last counted on, so a field is counted once per
    #: turn no matter how many times the instructions are refreshed. The
    #: background extractor refreshes them a second time when it lands.
    topic_offer_turn: dict[str, int] = field(default_factory=dict)

    # --- closing sequence -----------------------------------------------------
    callback_promised: bool = False
    #: True once Claire has actually offered the close ("anything else?" /
    #: "any questions?"). Retell required this before end_call was allowed.
    closing_offered: bool = False
    #: True once the caller has said they are finished. The agent still decides
    #: when to hang up - this only unlocks the end_call tool.
    caller_done: bool = False

    # --- runtime bookkeeping --------------------------------------------------
    reminder_count: int = 0
    disconnect_reason: str = ""
    #: True once end_call / decline / silence hangup has committed to closing.
    call_ended: bool = False
    #: True for LiveKit console sessions so 123-456-7890 style test numbers work.
    allow_test_phones: bool = False

    # ------------------------------------------------------------------
    def record_name(self, first: str | None, last: str | None) -> None:
        if first:
            self.first_name = first.strip()
        if last:
            self.last_name = last.strip()

    def record_phone(self, raw: str) -> bool:
        normalized = normalize_phone(raw, allow_test=self.allow_test_phones)
        if normalized is None:
            return False
        self.phone = normalized
        return True

    def mark_ended(self, reason: str) -> None:
        self.call_ended = True
        if reason:
            self.disconnect_reason = reason
        if self.ended_at is None:
            self.ended_at = time.time()

    # ------------------------------------------------------------------
    def missing_must_haves(self) -> list[str]:
        """Must-haves still outstanding (logs / post-call / end_call gate)."""
        missing: list[str] = []
        if not self.first_name:
            missing.append("their first name")
        if not self.last_name:
            missing.append("their last name")
        if not self.phone and self.phone_attempts < MAX_PHONE_ATTEMPTS:
            missing.append("a callback number they said out loud, with all 10 digits")
        elif not self.phone and self.phone_attempts >= MAX_PHONE_ATTEMPTS:
            # Three goes and it still is not a dialable number. On a real call
            # the caller repeated it four times and was asked a fifth; that is
            # not intake, it is an argument. Take what was heard, flag it, and
            # let the conversation move on — the attorney can confirm.
            pass
        elif not self.phone_read_back:
            missing.append("a read-back of the phone number so they can correct it")
        if self.other_party_required and not self.other_party_name:
            missing.append(
                "the other party's name for the conflict check (a name, or a clear "
                "'I don't know')"
            )
        if not self.incident_summary:
            missing.append("roughly what happened, when and where")
        if not self.callback_promised:
            missing.append(
                "telling them an attorney will review it and someone will call back, "
                "and asking whether they have any questions"
            )
        return missing

    def record_optional(self, name: str, value: Any) -> bool:
        """Store a non-must-have field. Returns True if this is new information."""
        if value in (None, "", []):
            return False
        if self.optional_fields.get(name) == value:
            return False
        self.optional_fields[name] = value
        return True

    def note_topics_offered(self, names: tuple[str, ...]) -> None:
        """Count one turn's worth of STILL UNKNOWN suggestions.

        Idempotent within a turn: `_refresh_instructions` runs once when the
        caller's turn completes and again when background extraction lands, and
        a field must not burn two of its three turns for one utterance.
        """
        for name in names:
            if self.topic_offer_turn.get(name) == self.user_turns:
                continue
            self.topic_offer_turn[name] = self.user_turns
            self.topic_offers[name] = self.topic_offers.get(name, 0) + 1

    def may_end_call(self) -> list[str]:
        """What still blocks `end_call`. Empty list = the agent may hang up.

        This is Retell's end_call tool description turned into a check. It is a
        *gate*, not a trigger: nothing here ever ends the call by itself, it only
        refuses when the model tries to end one too early.
        """
        blockers = self.missing_must_haves()
        if not self.caller_done:
            blockers.append(
                "the caller has not yet said they are finished - keep the "
                "conversation going until they do"
            )
        return blockers

    def collected_summary(self, still_unknown: tuple[str, ...] = ()) -> str:
        """The per-turn note handed to the model.

        Facts only. This used to restate the rules on every single turn -
        "CRITICAL: Never ask again...", a full-sentence description of each
        unasked topic, a "Guidance:" paragraph - which meant ~1,500 characters
        of instruction re-read on top of an already instruction-heavy prompt.
        The rules live in `OPERATING_BLOCK` now and are stated once; this is
        just the agent's memory of the call.
        """
        lines = ["ALREADY COLLECTED - do not ask for these again:"]
        lines.append(f"- name: {self.full_name or '(not yet)'}")
        if self.phone:
            rb = "confirmed" if self.phone_read_back else "not read back yet"
            lines.append(f"- phone: {self.phone} ({rb})")
        else:
            lines.append("- phone: (not yet)")
        if self.other_party_required or self.other_party_name:
            lines.append(f"- other party: {self.other_party_name or '(not yet)'}")
        if self.incident_summary:
            short = self.incident_summary
            if len(short) > 120:
                short = short[:117] + "..."
            lines.append(f"- what happened: {short}")
        else:
            lines.append("- what happened: (not yet)")

        for name, value in self.optional_fields.items():
            text = str(value)
            if len(text) > 80:
                text = text[:77] + "..."
            lines.append(f"- {name}: {text}")

        if self.closing_offered:
            lines.append("- you have already offered the close")
        elif still_unknown:
            lines.append("STILL UNKNOWN: " + ", ".join(still_unknown))

        if self.caller_done:
            leftover = self.missing_must_haves()
            if leftover:
                lines.append(
                    "- the caller is wrapping up; still needed: " + "; ".join(leftover)
                )
            else:
                lines.append(
                    "- the caller said they are finished — call end_call NOW "
                    "with a short goodbye and ZERO questions. Do not ask anything else."
                )

        if self.phone_attempts >= MAX_PHONE_ATTEMPTS and not self.phone:
            lines.append(
                f"- phone: {self.phone_attempts} attempts, still not a valid "
                f"number (heard: {self.phone_heard_raw or 'nothing usable'}). "
                "Stop asking and move on."
            )

        return "\n".join(lines)

    def next_missing_prompt(self) -> str:
        """One short line on where the call stands. Not a script."""
        missing = self.missing_must_haves()
        if not missing:
            if not self.caller_done:
                return (
                    "everything required is in. Do NOT hang up - stay with them "
                    "and let them lead until they say they are finished."
                )
            return "they are finished - call end_call NOW, short goodbye, no questions."
        return "still needed, whenever the conversation allows: " + "; ".join(missing)

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p)

    @property
    def duration_seconds(self) -> float:
        return (self.ended_at or time.time()) - self.started_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "room_name": self.room_name,
            "from_number": self.from_number,
            "to_number": self.to_number,
            "start_timestamp": int(self.started_at * 1000),
            "end_timestamp": int((self.ended_at or time.time()) * 1000),
            "duration_ms": int(self.duration_seconds * 1000),
            "case_type": self.case_type,
            "agent_name": self.agent_name,
            "handoffs": self.handoffs,
            "disconnection_reason": self.disconnect_reason,
            "collected": {
                **self.optional_fields,
                "user_fname": self.first_name,
                "user_lname": self.last_name,
                "user_phone": self.phone,
                # Only present when we gave up on getting a dialable number.
                # The firm should see what the caller actually said rather than
                # an empty field, but must know it was never verified.
                **(
                    {
                        "user_phone_unverified": self.phone_heard_raw,
                        "user_phone_attempts": self.phone_attempts,
                    }
                    if self.phone_unverified and not self.phone
                    else {}
                ),
                "other_party_name": self.other_party_name,
                "incident_summary": self.incident_summary,
            },
        }
