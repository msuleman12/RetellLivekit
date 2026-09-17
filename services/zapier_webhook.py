"""The flat payload shape the firm's existing Zap expects (from the old
ai-receptionist build): a `user` section, a section named after the agent,
and `metadata`."""

from datetime import datetime, timedelta, timezone
from typing import Any

from agents.user_data import CallData

# The Zap reads call_started_at as CST wall-clock time.
CST = timezone(timedelta(hours=-6))

# The old build's agent names, which existing Zap filters match on.
AGENT_NAME_BY_CASE_TYPE = {
    "accident": "accident",
    "employment": "employment",
    "premises": "premises_liability",
    "harassment": "sexual_harassment",
    "malpractice": "medical_malpractice",
}

_USER_FIELDS = {
    "user_fname", "user_lname", "user_phone", "user_email", "preferred_contact", "incident_city",
    "user_address", "user_dob",
}


def build_payload(data: CallData, custom: dict[str, Any], summary: str) -> dict[str, Any]:
    agent_name = AGENT_NAME_BY_CASE_TYPE[data.case_type]
    first = custom.get("user_fname") or ""
    last = custom.get("user_lname") or ""
    return {
        "session_id": data.call_id,
        "agent_name": agent_name,
        "call_started_at": datetime.fromtimestamp(data.started_at, tz=CST).isoformat(),
        "user": {
            "first_name": first,
            "last_name": last,
            "full_name": f"{first} {last}".strip(),
            "email": custom.get("user_email") or "",
            # The number the caller spoke, not caller ID (call_number).
            "phone_number": custom.get("user_phone") or "",
            "call_number": data.from_number,
            "preferred_contact": custom.get("preferred_contact") or "",
            "city": custom.get("incident_city") or "",
            "address": custom.get("user_address") or "",
            "dob": custom.get("user_dob") or "",
            "call_summary": summary,
            "is_test_call": False,
            "language": "en",
            "priority_level": "",
        },
        agent_name: {k: v for k, v in custom.items() if k not in _USER_FIELDS},
        "metadata": {
            "call_id": data.call_id,
            "room_name": data.room_name,
            "duration_ms": data.duration_ms,
            "disconnection_reason": data.disconnect_reason,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    }
