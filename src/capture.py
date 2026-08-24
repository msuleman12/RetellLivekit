"""Instant, inline fast-path capture from the caller's last utterance.

This used to be the *only* capture path, and that was the bug: a regex knows
exactly the phrasings someone thought to write down. A caller who answered
"Yes. Moss Ali." to "what's your name" matched nothing, so Claire asked again,
and the call read like a form.

`src/extract.py` is now the primary capture path — a model reads the transcript
after every turn and fills the same fields Retell's post-call schema uses. What
stays here is what is genuinely deterministic and worth having immediately,
without waiting for a model round-trip:

  * phone digits (spoken numbers, "oh" for zero, "double five", country codes),
  * a yes/no read-back confirmation,
  * an obvious "I don't know" for the conflict-check question,
  * a caller clearly signing off.

Nothing here ends a call. Only the agent's `end_call` tool does that.
"""

from __future__ import annotations

import logging
import re

from .state import (
    MAX_PHONE_ATTEMPTS,
    CallState,
    extract_phone_digits,
    normalize_phone,
    phone_digit_count,
)

logger = logging.getLogger("bushbush.capture")

_NAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:my\s+(?:full\s+)?name\s+is|i(?:'m|\s+am)|this\s+is)\s+"
        r"([A-Za-z][A-Za-z'\-]{1,30})\s+([A-Za-z][A-Za-z'\-]{1,30})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:my\s+name'?s)\s+"
        r"([A-Za-z][A-Za-z'\-]{1,30})\s+([A-Za-z][A-Za-z'\-]{1,30})\b",
        re.IGNORECASE,
    ),
    # A bare answer to "what's your name" — "Moss Ali.", "Yes. Moss Ali.",
    # "It's Jordan Lee". Anchored to the whole utterance and limited to a short
    # filler + exactly two words, so ordinary sentences cannot match. This is
    # the case the original patterns missed on a real call.
    re.compile(
        r"^(?:(?:yes|yeah|yep|sure|ok|okay|it'?s|this\s+is)[\s,.]+)?"
        r"([A-Za-z][A-Za-z'\-]{1,30})\s+([A-Za-z][A-Za-z'\-]{1,30})[\s.!,]*$",
        re.IGNORECASE,
    ),
)

_CONFIRM = re.compile(
    r"\b(yes|yep|yeah|yup|correct|right|ok|okay|sure|"
    r"that'?s\s+right|that\s+is\s+correct|sounds\s+right)\b",
    re.IGNORECASE,
)

_CLOSING_DONE = re.compile(
    r"\b("
    r"no(?:pe)?|nothing(?:\s+else)?|that'?s\s+(?:all|it)|i(?:'m|\s+am)\s+good|"
    r"no\s+questions?|all\s+set|we(?:'re|\s+are)\s+good|goodbye|bye|"
    r"thank(?:s| you)(?:\s+so\s+much)?(?:\s*,?\s*(?:bye|goodbye))?"
    r")\b",
    re.IGNORECASE,
)

_DONT_KNOW = re.compile(
    r"\b("
    r"i\s+don'?t\s+know|do\s+not\s+know|no\s+idea|not\s+sure|"
    r"can'?t\s+remember|prefer\s+not(?:\s+to\s+say)?|rather\s+not\s+say|"
    r"i\s+don'?t\s+have\s+(?:it|that|their\s+name)"
    r")\b",
    re.IGNORECASE,
)

# "The other driver was Alex Rivera" is at least as common as "the other
# driver's name is Alex Rivera", so the link verb is a set, not just "is".
# Optional possessive, then any of the link verbs: covers "the other driver's
# name is X", "the other driver name is X" and "the other driver was X".
_LINK = r"'?s?\s+(?:name\s+(?:is|was)\s+|is\s+|was\s+|were\s+|named\s+|called\s+)"
_PARTY_NAME = r"([A-Za-z][A-Za-z0-9'&\-.]*(?:\s+[A-Za-z0-9'&\-.]*){0,4})"

_OTHER_PARTY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"other\s+(?:driver|party|person|guy|lady|company){_LINK}{_PARTY_NAME}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?:their|his|her)\s+name\s+(?:is|was)\s+{_PARTY_NAME}",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:employer|company|business|hospital|doctor|clinic|facility|property|"
        rf"store|landlord|supervisor|manager|insurer|insurance){_LINK}{_PARTY_NAME}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?:i\s+(?:work|worked)\s+(?:for|at)|it\s+happened\s+at)\s+{_PARTY_NAME}",
        re.IGNORECASE,
    ),
)

_INCIDENT_HINTS = re.compile(
    r"\b("
    r"accident|crash|hit|rear|collision|slip|fell|fall|hurt|injur|"
    r"fired|terminat|harass|assault|doctor|hospital|surgery|negligen|"
    r"happened|yesterday|last\s+(?:week|month|tuesday|monday|night)|"
    r"intersection|freeway|highway|parking"
    r")\b",
    re.IGNORECASE,
)

# Words that must never be read as somebody's first or last name. The bare
# "First Last" pattern is permissive by design, so this list is what keeps
# "Thank you" and "car accident" out of the caller's name field.
_STOPWORDS = frozenset(
    {
        # original list
        "the", "other", "driver", "party", "accident", "car", "calling",
        "about", "best", "number", "phone", "full", "name", "please", "again",
        "someone", "something",
        # greetings / sign-offs
        "hi", "hello", "hey", "bye", "goodbye", "thanks", "thank", "you",
        "good", "morning", "afternoon", "evening", "night", "welcome",
        # affirm / deny / filler
        "yes", "yeah", "yep", "yup", "no", "nope", "nah", "sure", "ok", "okay",
        "right", "correct", "wrong", "maybe", "well", "um", "uh", "sorry",
        "i'm", "im", "i", "it's", "its", "that's", "thats", "he's", "she's",
        "we're", "they're", "don't", "dont", "can't", "cant", "isn't",
        # intake vocabulary
        "attorney", "lawyer", "law", "firm", "bush", "case", "claim", "help",
        "work", "job", "employer", "company", "business", "hospital", "doctor",
        "clinic", "insurance", "police", "report", "injury", "injured", "hurt",
        "crash", "collision", "slip", "fall", "fell", "harassment", "assault",
        "malpractice", "premises", "email", "address", "time", "today",
        "yesterday", "tomorrow", "week", "month", "year", "question",
        "questions", "everything", "nothing", "anything",
    }
)

_FAREWELL = "Thanks for calling Bush and Bush. Take care."


def _looks_like_person_name(first: str, last: str) -> bool:
    if first.lower() in _STOPWORDS or last.lower() in _STOPWORDS:
        return False
    if first.lower() == last.lower():
        return False
    if len(first) < 2 or len(last) < 2:
        return False
    return first[0].isalpha() and last[0].isalpha()


def extract_name(text: str) -> tuple[str, str] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    # A turn carrying digits is a phone number or a date, never a name.
    if sum(ch.isdigit() for ch in raw) >= 3:
        return None
    for pattern in _NAME_PATTERNS:
        match = pattern.search(text.strip())
        if not match:
            continue
        first, last = match.group(1).strip(), match.group(2).strip()
        if _looks_like_person_name(first, last):
            return first.title(), last.title()
    return None


def is_affirmation(text: str) -> bool:
    return bool(_CONFIRM.search(text or ""))


def is_closing_done(text: str) -> bool:
    """Caller indicates they are finished / have no more questions."""
    raw = (text or "").strip()
    if not raw:
        return False
    # Short "no" alone after closing question
    if raw.lower() in {"no", "nope", "nah", "bye", "goodbye"}:
        return True
    return bool(_CLOSING_DONE.search(raw))


def extract_other_party(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if _DONT_KNOW.search(raw):
        return "I don't know"
    for pattern in _OTHER_PARTY_PATTERNS:
        match = pattern.search(raw)
        if not match:
            continue
        name = re.sub(r"\s+", " ", match.group(1)).strip(" .,")
        if len(name) < 2:
            continue
        lowered = name.lower()
        if "bush and bush" in lowered or "bush & bush" in lowered:
            continue
        if lowered in _STOPWORDS:
            continue
        return name
    return None


def utterance_text(new_message: object) -> str:
    """Plain text of a ChatMessage, whichever shape the plugin produced."""
    text = (getattr(new_message, "text_content", None) or "").strip()
    if text:
        return text
    content = getattr(new_message, "content", None)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return " ".join(str(p) for p in content).strip()
    return ""


# Kept for callers that still import the private name.
_utterance_text = utterance_text


def auto_capture_from_utterance(state: CallState, text: str) -> list[str]:
    """Update CallState from raw user text. Returns notes of what changed."""
    if not text or not text.strip() or state.call_ended:
        return []

    notes: list[str] = []
    raw = text.strip()
    heard_phone = normalize_phone(raw)
    affirmed = is_affirmation(raw)

    if not (state.first_name and state.last_name):
        parsed = extract_name(raw)
        if parsed:
            state.record_name(parsed[0], parsed[1])
            notes.append(f"name={state.full_name}")
            logger.info("auto-captured name: %s", state.full_name)

    heard_digits = extract_phone_digits(raw)

    if heard_phone:
        if not state.phone:
            state.phone = heard_phone
            state.phone_heard_raw = heard_digits
            notes.append(f"phone={heard_phone}")
            logger.info("auto-captured phone: %s", heard_phone)
            if affirmed:
                state.phone_read_back = True
                notes.append("phone_read_back=confirmed")
                logger.info("auto-confirmed phone (affirmation + digits)")
        elif state.phone == heard_phone and not state.phone_read_back:
            state.phone_read_back = True
            notes.append("phone_read_back=confirmed (repeated)")
            logger.info("auto-confirmed phone via repeated digits")
        elif state.phone != heard_phone and not state.phone_read_back:
            # A correction. The old code locked the first number in forever, so
            # a caller fixing a mis-hear was ignored while the agent kept
            # asking. An unconfirmed number is always replaceable.
            logger.info("phone corrected: %s -> %s", state.phone, heard_phone)
            state.phone = heard_phone
            state.phone_heard_raw = heard_digits
            notes.append(f"phone={heard_phone} (corrected)")
    elif len(heard_digits) >= 7:
        # Enough digits to be an attempt at a number, but not a usable one.
        # Count it so the agent knows when to stop asking, and keep what was
        # said so the firm is not left with an empty field.
        state.phone_attempts += 1
        state.phone_heard_raw = heard_digits
        notes.append(f"phone_attempt={state.phone_attempts} (heard {heard_digits})")
        logger.info(
            "phone attempt %d unusable: heard %r", state.phone_attempts, heard_digits
        )
        if state.phone_attempts >= MAX_PHONE_ATTEMPTS and not state.phone:
            state.phone_unverified = True
            notes.append("phone_unverified=True (stopped asking)")
            logger.warning(
                "giving up on a valid phone after %d attempts; recording %r "
                "unverified",
                state.phone_attempts,
                heard_digits,
            )
    elif (
        state.phone
        and not state.phone_read_back
        and affirmed
        and phone_digit_count(raw) < 7
    ):
        state.phone_read_back = True
        notes.append("phone_read_back=confirmed (yes)")
        logger.info("auto-confirmed phone via affirmation")

    if state.other_party_required and not state.other_party_name:
        other = extract_other_party(raw)
        if other:
            if state.full_name and other.lower() == state.full_name.lower():
                pass
            else:
                state.other_party_name = other
                notes.append(f"other_party={other}")
                logger.info("auto-captured other party: %s", other)
    elif not state.other_party_required and not state.other_party_name and _DONT_KNOW.search(raw):
        state.other_party_name = "I don't know"
        notes.append("other_party=I don't know")

    if not state.incident_summary:
        # Prefer story-like turns: long enough, not just yes/phone/name.
        if (
            len(raw) >= 40
            and not heard_phone
            and extract_name(raw) is None
            and (_INCIDENT_HINTS.search(raw) or (state.first_name and state.phone))
        ):
            state.incident_summary = raw[:500]
            notes.append("incident=captured")
            logger.info("auto-captured incident summary (%d chars)", len(raw))

    # Closing: core must-haves in + caller signs off. This only records that
    # they sounded finished; the agent still chooses whether to call end_call,
    # and `src/extract.py` can revoke it if they start talking again.
    core_missing = [
        m
        for m in state.missing_must_haves()
        if "attorney will review" not in m and "questions" not in m
    ]
    if not core_missing and is_closing_done(raw):
        if not state.callback_promised:
            state.callback_promised = True
            notes.append("callback_promised=True")
            logger.info("auto-marked callback promised (caller done)")
        if state.closing_offered and not state.caller_done:
            state.caller_done = True
            notes.append("caller_done=True")

    return notes


def should_hangup_after_capture(state: CallState) -> bool:
    """Advisory only: True when every must-have is in.

    This used to be wired straight to a hangup, which is how calls ended while
    the caller was still mid-sentence. Nothing calls it to end a call any more —
    it survives as a readable check for tests and logging. The real gate is
    `CallState.may_end_call()`, consulted by the agent's `end_call` tool.
    """
    return (not state.call_ended) and (not state.missing_must_haves())


def default_farewell() -> str:
    return _FAREWELL
