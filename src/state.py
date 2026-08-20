"""Per-call state (LiveKit `userdata`).

Retell tracked the must-haves only inside the prompt ("FORBIDDEN - end_call").
Here they are also written via `record_*` tools into `CallState` for post-call
analysis and for per-turn ALREADY COLLECTED injection so Claire does not re-ask.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

CASE_TYPES = ("accident", "employment", "premises", "harassment", "malpractice", "other")

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


def is_valid_us_number(digits: str) -> bool:
    """NANP validity: 10 digits, area code and exchange both start 2-9.

    Retell's prompt said "ignore country code 1 if they say it" and "must be 10
    US digits".  Without this check a caller who says "+1 290-909-490" (country
    code plus only nine digits) produced the plausible-looking but impossible
    `1290909490`, and both the agent and the post-call webhook accepted it.
    """
    if len(digits) != 10 or not digits.isdigit():
        return False
    return digits[0] in "23456789" and digits[3] in "23456789"


def normalize_phone(raw: str | None) -> str | None:
    """Return a valid 10-digit US number, or None if we do not have one yet."""
    digits = extract_phone_digits(raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    # If STT appended extra noise digits, prefer the last 10 of a longer string
    # only when it looks like country-code + number was mangled beyond 11.
    if len(digits) > 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) > 10:
        # Prefer a contiguous valid 10-digit US number if present; else last 10.
        for match in re.finditer(r"(?=(\d{10}))", digits):
            if is_valid_us_number(match.group(1)):
                return match.group(1)
        digits = digits[-10:]
    return digits if is_valid_us_number(digits) else None


def phone_digit_count(raw: str | None) -> int:
    """How many digits we heard (after spoken-word conversion), max useful for retries."""
    digits = extract_phone_digits(raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return len(digits)


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

    # ------------------------------------------------------------------
    def record_name(self, first: str | None, last: str | None) -> None:
        if first:
            self.first_name = first.strip()
        if last:
            self.last_name = last.strip()

    def record_phone(self, raw: str) -> bool:
        normalized = normalize_phone(raw)
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
        if not self.phone:
            missing.append("a callback number they said out loud, with all 10 digits")
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
        """Per-turn system note so the model does not re-ask known fields."""
        lines = ["ALREADY COLLECTED — do NOT re-ask these:"]
        if self.full_name:
            lines.append(f"- name: {self.full_name}")
        else:
            lines.append("- name: (missing)")
        if self.phone:
            rb = "read-back done" if self.phone_read_back else "needs one read-back confirm"
            lines.append(f"- phone: {self.phone} ({rb})")
        else:
            lines.append("- phone: (missing)")
        if self.other_party_required:
            if self.other_party_name:
                lines.append(f"- other party: {self.other_party_name}")
            else:
                lines.append("- other party: (missing)")
        else:
            lines.append(
                f"- other party: {self.other_party_name or '(optional / not required)'}"
            )
        if self.incident_summary:
            short = self.incident_summary
            if len(short) > 120:
                short = short[:117] + "..."
            lines.append(f"- incident: {short}")
        else:
            lines.append("- incident: (missing)")
        if self.callback_promised:
            lines.append("- closing promise / questions asked: yes")
        else:
            lines.append("- closing promise / questions asked: (not yet)")

        for name, value in self.optional_fields.items():
            text = str(value)
            if len(text) > 90:
                text = text[:87] + "..."
            lines.append(f"- {name}: {text}")

        if self.asked_topics:
            lines.append(
                "Already raised (do not raise again): "
                + ", ".join(sorted(self.asked_topics))
            )

        if still_unknown:
            lines.append("")
            lines.append(
                "STILL UNKNOWN — a menu of things you could ask about, in no "
                "particular order. Pick at most one, only if it fits what they "
                "just said. Skipping all of them is fine:"
            )
            for topic in still_unknown:
                lines.append(f"  · {topic}")

        lines.append("")
        lines.append(f"Guidance: {self.next_missing_prompt()}")
        lines.append(
            "CRITICAL: Never ask again for any field above that is not marked (missing)."
        )
        return "\n".join(lines)

    def next_missing_prompt(self) -> str:
        """Soft guidance, never a script.

        The old version told the model to "ask only for the first missing
        must-have", which turned the call into a fixed questionnaire. Retell
        never did that - its prompt called the order "an order that feels
        human" and left the choice to the model.
        """
        missing = self.missing_must_haves()
        if not missing:
            if not self.caller_done:
                return (
                    "everything required is in. Do NOT hang up. Stay with the "
                    "caller, answer what they ask, and let them lead until they "
                    "tell you they are finished."
                )
            return (
                "the caller has said they are done — thank them and call end_call "
                "with a short goodbye (no questions)."
            )
        if len(missing) == 1:
            return (
                f"still needed at some point: {missing[0]}. Ask for it when the "
                "conversation gives you a natural opening, not as an interruption."
            )
        return (
            "still needed at some point: "
            + "; ".join(missing)
            + ". Weave these in naturally, one per turn, in whatever order the "
            "conversation makes easy. Never announce them as a list."
        )

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
                "other_party_name": self.other_party_name,
                "incident_summary": self.incident_summary,
            },
        }
