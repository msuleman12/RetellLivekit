"""Who may be transferred to a human, and when.

The firm's rule, carried over from the ai-receptionist build: during office
hours the three urgent practice areas go straight to an attorney, premises and
employment are handled by the AI unless the caller asks for a person, and
outside office hours nothing transfers at all.

Every check here is local arithmetic - no I/O - so it can run on the call path.
"""

import logging
from datetime import datetime

from config import OFFICE_HOURS
from utils.dates import FIRM_TZ

logger = logging.getLogger("intake.office_hours")

# Urgent matters: an attorney takes these live while the office is open.
AUTO_ESCALATE_CASES = frozenset({"accident", "harassment", "malpractice"})

# The AI runs these intakes; it transfers only if the caller asks for a person.
AI_HANDLED_CASES = frozenset({"premises", "employment"})

OFFICE_HOURS_SENTENCE = (
    "Our attorneys are available Monday through Friday, "
    "8 AM to 5 PM Central Time."
)


def is_office_hours(now: datetime | None = None) -> bool:
    """True Mon-Fri between the configured hours, in the firm's local time."""
    now = now.astimezone(FIRM_TZ) if now else datetime.now(FIRM_TZ)
    return (
        now.weekday() in OFFICE_HOURS.days
        and OFFICE_HOURS.start_hour <= now.hour < OFFICE_HOURS.end_hour
    )


def can_transfer_now(case_type: str, user_requested: bool = False) -> tuple[bool, str]:
    """Returns (may transfer, reason code). The reason code is for the logs."""
    if not is_office_hours():
        return False, "outside_office_hours"

    if case_type in AUTO_ESCALATE_CASES:
        return True, "user_requested" if user_requested else "auto_escalate"

    # Premises and employment, or anything unrecognised: only on request.
    if user_requested:
        return True, "user_requested"
    return False, "ai_handles_this_case_type"


def transfer_denial_message(case_type: str) -> str:
    """What to tell a caller who asked for an attorney but cannot have one now."""
    if not is_office_hours():
        return (
            "I understand you'd like to speak with an attorney. "
            f"{OFFICE_HOURS_SENTENCE} I'm here to help you right now, and I'll make "
            "sure your information is with the legal team first thing."
        )
    return (
        "Let me keep going with your details, and I'll make sure the right "
        "person picks this up."
    )
