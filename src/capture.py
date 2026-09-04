"""Instant, inline fast-path capture from the caller's last utterance.

This used to be the *only* capture path, and that was the bug: a regex knows
exactly the phrasings someone thought to write down. A caller who answered
"Yes. Moss Ali." to "what's your name" matched nothing, so Claire asked again,
and the call read like a form.

`src/extract.py` is now the primary capture path — a model reads the transcript
after every turn and fills the same fields Retell's post-call schema uses. What
stays here is what is genuinely deterministic and worth having immediately,
without waiting for a model round-trip:

  * names ("Name is John Smith", a bare "Moss Ali", a confirmed read-back),
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
    mask_phone,
    normalize_phone,
    phone_digit_count,
)

logger = logging.getLogger("bushbush.capture")

# Deepgram often inserts a period when the caller pauses between names:
# "So my name is Johns. Smith."
_NAME_WORD = r"([A-Za-z][A-Za-z'\-]{1,30})"
_NAME_SEP = r"\s*[.,]?\s+"

_NAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "Name is John Smith." / "The name is John Smith" / "Full name is …"
    # Start-anchored so "the other driver's name is Alex Rivera" cannot match.
    re.compile(
        r"(?:^|(?<=[.!?]\s))(?:(?:yes|yeah|yep|sure|ok|okay|so|well|and)[\s,.]+)?"
        r"(?:(?:my|the)\s+)?(?:full\s+)?name(?:'?s|\s+is)\s+"
        rf"{_NAME_WORD}{_NAME_SEP}{_NAME_WORD}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:my\s+(?:full\s+)?name\s+is|i(?:'m|\s+am)|this\s+is)\s+"
        rf"{_NAME_WORD}{_NAME_SEP}{_NAME_WORD}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:my\s+name'?s)\s+{_NAME_WORD}{_NAME_SEP}{_NAME_WORD}\b",
        re.IGNORECASE,
    ),
    # A bare answer to "what's your name" — "Moss Ali.", "Yes. Moss Ali.",
    # "It's Jordan Lee". Anchored to the whole utterance and limited to a short
    # filler + exactly two words, so ordinary sentences cannot match. This is
    # the case the original patterns missed on a real call.
    re.compile(
        r"^(?:(?:yes|yeah|yep|sure|ok|okay|so|well|and|it'?s|this\s+is)[\s,.]+)?"
        rf"{_NAME_WORD}{_NAME_SEP}{_NAME_WORD}[\s.!,]*$",
        re.IGNORECASE,
    ),
)

# "My name is Mayagoms." — first name only; last name stays missing.
_FIRST_ONLY_NAME = re.compile(
    r"(?:my\s+(?:full\s+)?name(?:'?s|\s+is)|i(?:'m|\s+am)|this\s+is)\s+"
    rf"{_NAME_WORD}[\s.!,]*$",
    re.IGNORECASE,
)

# Claire repeating the name back: "your full name is John Smith, right?"
_AGENT_NAME_READBACK = re.compile(
    r"(?:your\s+(?:full\s+)?name\s+is|your\s+name\s+as|"
    r"i\s+have\s+(?:you|your\s+name)\s+as)\s+"
    rf"{_NAME_WORD}{_NAME_SEP}{_NAME_WORD}\b",
    re.IGNORECASE,
)

_CONFIRM = re.compile(
    r"\b(yes|yep|yeah|yup|correct|right|ok|okay|sure|"
    r"that'?s\s+right|that\s+is\s+correct|sounds\s+right)\b",
    re.IGNORECASE,
)

_CLOSING_DONE = re.compile(
    r"\b("
    r"nothing(?:\s+else)?|that'?s\s+(?:all|it|everything)|"
    r"that(?:\s+will|'ll)\s+be\s+all|"
    r"i(?:'m|\s+am)\s+(?:good|done|finished|all\s+set)|"
    r"we(?:'re|\s+are)\s+(?:good|done|finished)|"
    r"no\s+(?:more\s+)?questions?|all\s+set|goodbye|bye|"
    r"thank(?:s| you)(?:\s+so\s+much)?(?:\s*,?\s*(?:bye|goodbye))?|"
    r"(?:i\s+)?don'?t\s+(?:want\s+to\s+)?(?:add|share|ask)"
    r"(?:\s+or\s+(?:add|share|ask))?(?:\s+anything(?:\s+else)?)?|"
    r"(?:do\s+not|don't)\s+have\s+(?:anything|any(?:thing)?\s+else)|"
    r"(?:i\s+)?don'?t\s+have\s+(?:any\s+)?(?:more\s+)?questions?|"
    r"haven'?t\s+(?:got\s+)?any\s+questions?|"
    r"nothing\s+(?:else\s+)?to\s+(?:add|share|ask)|"
    r"that'?s\s+all\s+from\s+(?:my|our)\s+side|"
    r"it(?:'?s|\s+is)\s+everything(?:\s+from\s+(?:my|our)\s+side)?"
    r"|"
    r"(?:you\s+can\s+)?hang\s+up|"
    r"no\s+(?:thank\s+you|thanks)|"
    r"take\s+care|take\s+cash"
    r")\b",
    re.IGNORECASE,
)

_CLOSING_QUESTION = re.compile(
    r"\b(?:anything else|any questions|before we finish|"
    r"is there anything else|anything you'd like to add|"
    r"anything you(?: would|'d) like to (?:add|ask)|"
    r"attorney will review)\b",
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

# Claire asking for the store / property / employer, or reading one back.
_PLACE_QUESTION = re.compile(
    r"\b(store|property|business|grocery|employer|company|hospital|clinic|"
    r"where (?:you|it) (?:fell|happened|slipped)|"
    r"name of (?:the )?(?:place|store|property|business|grocery))\b",
    re.IGNORECASE,
)
_AGENT_PLACE_READBACK = re.compile(
    r"(?:you mentioned|i have(?:\s+it)?\s+as|the (?:store|property|business|"
    r"grocery store|employer|company) (?:is|was|called|named))\s+"
    r"(.+?)(?:[,?]|\s+is\s+that|\s+right\s*[?]?\s*$|$)",
    re.IGNORECASE,
)

# Strong enough to be "what happened", not a greeting that happens to
# mention yesterday. "Yesterday. I need some help from your side." must
# not lock incident_summary before the actual story arrives.
#
# Trailing `\b` after a stem like `harass` used to miss "harassing" /
# "harassment", so a full workplace-harassment story never landed and Claire
# asked "what happened" again after the caller had signed off.
_INCIDENT_STRONG = re.compile(
    r"\b(?:"
    r"accident|crash(?:ed)?|hit|rear-?end(?:ed)?|collision|"
    r"slipp?(?:ed|ing)?|fell|falling|falls?|"
    r"hurt|injur\w*|"
    r"fir(?:ed|ing)|terminat\w*|"
    r"harass\w*|assault\w*|"
    r"doctor|hospital|surgery|negligen\w*|"
    r"intersection|freeway|highway|parking"
    r")\b",
    re.IGNORECASE,
)

_STORY_QUESTION = re.compile(
    r"\b(?:what happened|tell me (?:a bit )?about what happened|"
    r"describe what happened|what happened and when|"
    r"when (?:and where )?(?:did )?(?:this|it) happen|"
    r"around when,? and where)\b",
    re.IGNORECASE,
)

_CALLBACK_PROMISED = re.compile(
    r"attorney will review|someone (?:will|from our office will) "
    r"(?:call|get back|be in touch)",
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
        match = pattern.search(raw)
        if not match:
            continue
        first, last = match.group(1).strip(), match.group(2).strip()
        if _looks_like_person_name(first, last):
            return first, last
    only = _FIRST_ONLY_NAME.search(raw)
    if only:
        first = only.group(1).strip()
        if (
            len(first) >= 2
            and first[0].isalpha()
            and first.lower() not in _STOPWORDS
        ):
            return first, ""
    return None


def extract_name_from_agent_readback(text: str) -> tuple[str, str] | None:
    """Name Claire just read back, e.g. 'your full name is John Smith, right?'."""
    raw = (text or "").strip()
    if not raw:
        return None
    match = _AGENT_NAME_READBACK.search(raw)
    if not match:
        return None
    first, last = match.group(1).strip(), match.group(2).strip()
    if _looks_like_person_name(first, last):
        return first, last
    return None


def is_affirmation(text: str) -> bool:
    return bool(_CONFIRM.search(text or ""))


def is_closing_done(text: str, previous_agent_text: str = "") -> bool:
    """Caller indicates they are finished / have no more questions."""
    raw = (text or "").strip()
    if not raw:
        return False
    short = raw.lower().strip(" .,!")
    # Bare "no" after "have you reported this?" is an answer, not a sign-off.
    # Only treat it as done when Claire just offered the close.
    if short in {"no", "nope", "nah"}:
        prev = previous_agent_text or ""
        return bool(_CLOSING_QUESTION.search(prev) or _CALLBACK_PROMISED.search(prev))
    if short in {
        "bye",
        "goodbye",
        "take care",
        "take cash",
        "that's all",
        "thats all",
        "that's it",
        "thats it",
        "that's everything",
        "that's everything from my side",
        "thats everything from my side",
        "it's everything from my side",
        "its everything from my side",
        "i'm done",
        "im done",
        "i am done",
        "i'm finished",
        "nothing else",
        "bye bye",
        "hang up",
        "you can hang up",
        "i don't have any questions",
        "i dont have any questions",
    }:
        return True
    return bool(_CLOSING_DONE.search(raw))


def _is_bare_yes_no(text: str) -> bool:
    return (text or "").lower().strip(" .,!") in {
        "yes",
        "yeah",
        "yep",
        "yup",
        "no",
        "nope",
        "nah",
        "ok",
        "okay",
        "sure",
        "right",
        "correct",
    }


def _eligible_incident_text(raw: str) -> bool:
    """True when this turn could be the story, not a name, number, or sign-off."""
    if len(raw) < 12:
        return False
    if extract_name(raw) is not None:
        return False
    if phone_digit_count(raw) >= 7:
        return False
    if _is_bare_yes_no(raw):
        return False
    if is_closing_done(raw) and len(raw.split()) <= 10:
        return False
    return True


def user_texts_from_chat(turn_ctx: object, latest: str = "") -> list[str]:
    """Caller turns in `turn_ctx`, plus `latest` if it is not already last."""
    texts: list[str] = []
    for item in getattr(turn_ctx, "items", None) or []:
        if getattr(item, "role", None) != "user":
            continue
        text = utterance_text(item)
        if text:
            texts.append(text)
    latest = (latest or "").strip()
    if latest and texts[-1:] != [latest]:
        texts.append(latest)
    return texts


def backfill_name_from_context(
    state: CallState, user_texts: list[str]
) -> list[str]:
    """Pick up a name said earlier if this turn's fast-path missed it.

    "So my name is Johns. Smith." used to miss because of the period, so
    ALREADY COLLECTED kept saying `(not yet)` and Claire asked name and phone
    again after the caller had already given both.
    """
    if state.first_name and state.last_name:
        return []
    for raw in user_texts:
        parsed = extract_name(raw or "")
        if not parsed:
            continue
        first, last = parsed
        if not first:
            continue
        if last or not state.first_name:
            state.record_name(first, last)
            label = state.full_name or first
            logger.info("backfilled name (%s)", label)
            return [f"name={label}"]
    return []


def backfill_incident_from_context(
    state: CallState, user_texts: list[str]
) -> list[str]:
    """Fill `incident_summary` from earlier caller turns if this turn missed it.

    Live extract is off by default, so a story that arrived before the name
    question — or was split across short turns — never reached CallState.
    Without this, sign-off still shows `what happened: (not yet)` and Claire
    asks the story again.
    """
    if state.incident_summary:
        return []

    for raw in user_texts:
        raw = (raw or "").strip()
        if not _eligible_incident_text(raw):
            continue
        if _INCIDENT_STRONG.search(raw) and len(raw) >= 20:
            state.incident_summary = raw[:500]
            logger.info("backfilled incident summary (%d chars)", len(raw))
            return ["incident=backfilled"]

    parts: list[str] = []
    for raw in user_texts:
        raw = (raw or "").strip()
        if not raw:
            continue
        if extract_name(raw) is not None or phone_digit_count(raw) >= 7:
            continue
        if is_closing_done(raw) and len(raw.split()) <= 10:
            continue
        parts.append(raw)
    blob = " ".join(parts)
    if _INCIDENT_STRONG.search(blob) and len(blob) >= 24:
        state.incident_summary = blob[:500]
        logger.info(
            "backfilled incident summary from conversation (%d chars)", len(blob)
        )
        return ["incident=backfilled"]
    return []


def extract_other_party(text: str, previous_agent_text: str = "") -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if _DONT_KNOW.search(raw):
        return "I don't know"

    if previous_agent_text and _PLACE_QUESTION.search(previous_agent_text):
        if is_affirmation(raw) and phone_digit_count(raw) < 3:
            mentioned = extract_other_party_from_agent_readback(previous_agent_text)
            if mentioned:
                return mentioned
        if phone_digit_count(raw) < 3:
            cleaned = re.sub(
                r"^(?:(?:yes|yeah|yep|sure|ok|okay)[\s,.]+)?(?:it(?:'?s|\s+was|\s+is)\s+)?"
                r"(?:a |an |the )?",
                "",
                raw,
                flags=re.IGNORECASE,
            ).strip(" .,")
            if (
                len(cleaned) >= 3
                and cleaned.lower() not in _STOPWORDS
                and cleaned.lower().strip(" .,!")
                not in {
                    "yes",
                    "yeah",
                    "yep",
                    "yup",
                    "no",
                    "nope",
                    "nah",
                    "ok",
                    "okay",
                    "sure",
                    "right",
                    "correct",
                }
            ):
                return cleaned

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


def extract_other_party_from_agent_readback(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    match = _AGENT_PLACE_READBACK.search(raw)
    if not match:
        return None
    name = re.sub(r"\s+", " ", match.group(1)).strip(" .,")
    if len(name) < 3:
        return None
    if name.lower() in _STOPWORDS:
        return None
    return name


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


def auto_capture_from_utterance(
    state: CallState,
    text: str,
    previous_agent_text: str = "",
) -> list[str]:
    """Update CallState from raw user text. Returns notes of what changed."""
    if not text or not text.strip() or state.call_ended:
        return []

    notes: list[str] = []
    raw = text.strip()
    heard_phone = normalize_phone(raw, allow_test=state.allow_test_phones)
    affirmed = is_affirmation(raw)

    if not (state.first_name and state.last_name):
        parsed = extract_name(raw)
        if parsed is None and affirmed and previous_agent_text:
            parsed = extract_name_from_agent_readback(previous_agent_text)
        if parsed:
            state.record_name(parsed[0], parsed[1])
            if state.first_name and state.last_name:
                notes.append(f"name={state.full_name}")
            else:
                notes.append(f"name={state.first_name} (last name not yet)")
            logger.info("auto-captured name")

    heard_digits = extract_phone_digits(raw)

    if heard_phone:
        if not state.phone:
            state.phone = heard_phone
            state.phone_heard_raw = heard_digits
            notes.append(f"phone={heard_phone}")
            logger.info("auto-captured phone %s", mask_phone(heard_phone))
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
            logger.info(
                "phone corrected %s -> %s",
                mask_phone(state.phone),
                mask_phone(heard_phone),
            )
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
        logger.info("phone attempt %d unusable (%d digits)", state.phone_attempts, len(heard_digits))
        if state.phone_attempts >= MAX_PHONE_ATTEMPTS and not state.phone:
            state.phone_unverified = True
            notes.append("phone_unverified=True (stopped asking)")
            logger.warning(
                "giving up on a valid phone after %d attempts; keeping unverified digits",
                state.phone_attempts,
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

    existing_is_caller = bool(
        state.other_party_name
        and state.full_name
        and state.full_name.lower() in state.other_party_name.lower()
    )
    if not state.other_party_name or existing_is_caller:
        other = extract_other_party(raw, previous_agent_text)
        if other is None and affirmed and previous_agent_text:
            other = extract_other_party_from_agent_readback(previous_agent_text)
        if other:
            if state.full_name and state.full_name.lower() in other.lower():
                pass
            else:
                state.other_party_name = other
                notes.append(f"other_party={other}")
                logger.info("auto-captured other party")

    story_asked = bool(
        previous_agent_text and _STORY_QUESTION.search(previous_agent_text)
    )
    if _eligible_incident_text(raw) and (
        (_INCIDENT_STRONG.search(raw) and len(raw) >= 20) or (story_asked and len(raw) >= 12)
    ):
        if not state.incident_summary:
            state.incident_summary = raw[:500]
            notes.append("incident=captured")
            logger.info("auto-captured incident summary (%d chars)", len(raw))
        elif not _INCIDENT_STRONG.search(state.incident_summary):
            # First capture was a date/greeting; the real story arrived later.
            state.incident_summary = raw[:500]
            notes.append("incident=updated")
            logger.info("replaced weak incident summary (%d chars)", len(raw))
        elif _INCIDENT_STRONG.search(raw) and len(raw) >= len(state.incident_summary) + 30:
            # Keep the fuller telling (e.g. when/where) once they give it.
            state.incident_summary = raw[:500]
            notes.append("incident=updated")
            logger.info("replaced short incident summary (%d chars)", len(raw))

    if previous_agent_text and _CALLBACK_PROMISED.search(previous_agent_text):
        if not state.callback_promised:
            state.callback_promised = True
            notes.append("callback_promised=True")
        if not state.closing_offered:
            state.closing_offered = True
            notes.append("closing_offered=True")

    # Closing: core must-haves in + caller signs off. This only records that
    # they sounded finished; the agent still chooses whether to call end_call,
    # and `src/extract.py` can revoke it if they start talking again.
    #
    # `closing_offered` used to be set only by the live extractor. That flag is
    # off by default, so "nothing else" never unlocked `caller_done` and the
    # end_call tool kept refusing. If the must-haves are in and they clearly
    # declined further questions, that *is* the close.
    core_missing = [
        m
        for m in state.missing_must_haves()
        if "attorney will review" not in m and "questions" not in m
    ]
    if not core_missing and is_closing_done(raw, previous_agent_text):
        if not state.callback_promised:
            state.callback_promised = True
            notes.append("callback_promised=True")
            logger.info("auto-marked callback promised (caller done)")
        if not state.closing_offered:
            state.closing_offered = True
            notes.append("closing_offered=True")
        if not state.caller_done:
            state.caller_done = True
            notes.append("caller_done=True")
            logger.info("caller signed off — end_call unlocked")

    return notes


def default_farewell() -> str:
    return _FAREWELL
