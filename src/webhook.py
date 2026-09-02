"""Zapier webhook - the payload shape the firm's existing Zap already expects.

The `ai-receptionist` build posted a flat, Zapier-friendly object to
`ZAPIER_WEBHOOK_URL`: a `user` section, a section named after the agent, and a
`metadata` section (`services/zapier_webhook.py`). This port posts the
Retell-shaped `call_analyzed` payload to `POST_CALL_WEBHOOK_URL` instead -
the right shape for a Retell replacement, and the wrong shape for a Zap that
was built against the old one.

So this module reproduces the old payload exactly, built from LiveKit's
`CallState`, and posts it to the same `ZAPIER_WEBHOOK_URL`. The two webhooks
are independent: either can be configured without the other, and neither a
failure nor an absent URL on one affects the other or the call.

Three deliberate differences from `services/zapier_webhook.py`:

* **aiohttp, not `requests`.** This runs inside the agent's event loop during
  job shutdown. A synchronous POST would block every other coroutine on that
  loop for up to the full timeout, including the local record write.
* **Agent names are translated back to the old build's spelling** - this port
  calls them `premises`, `harassment`, `malpractice`; the old one called them
  `premises_liability`, `sexual_harassment`, `medical_malpractice`. An
  existing Zap filters on the old value, so it is the old value that goes on
  the wire.
* **`address` and `dob` are always empty.** The old build collected them; the
  Retell agents this port reproduces never asked for either. The keys are kept
  so the payload shape does not change under the Zap, but nothing fills them.

Nothing here decides what the agent says or when a call ends. It only reports.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from . import settings
from .state import CallState

logger = logging.getLogger("bushbush.webhook")

#: The old build stamped call_started_at in CST (UTC-6), not UTC, and the Zap
#: reads it as a wall-clock time. Keep the same conversion or every timestamp
#: in the firm's sheet shifts by six hours.
CST = timezone(timedelta(hours=-6))

#: This port's case_type -> the agent_name the old build sent.
_AGENT_NAME_BY_CASE_TYPE = {
    "accident": "accident",
    "employment": "employment",
    "premises": "premises_liability",
    "harassment": "sexual_harassment",
    "malpractice": "medical_malpractice",
}

#: Belong to the `user` section; excluded from the agent-specific section so
#: they are not sent twice under two different names.
_USER_LEVEL_FIELDS = frozenset(
    {"user_fname", "user_lname", "user_phone", "user_email", "preferred_contact"}
)


def _to_cst(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CST)


def _user_object(state: CallState, custom: dict[str, Any], summary: str) -> dict[str, Any]:
    """The old build's `user` section, filled from CallState."""
    optional = state.optional_fields
    return {
        "first_name": state.first_name or "",
        "last_name": state.last_name or "",
        "full_name": state.full_name or "",
        "email": custom.get("user_email") or optional.get("user_email") or "",
        # phone_number is the number the caller spoke; call_number is the
        # number they happened to be calling from. The old build kept them
        # apart and so does this - caller ID is not a callback number.
        "phone_number": state.phone or "",
        "call_number": state.from_number or "",
        "preferred_contact": custom.get("preferred_contact")
        or optional.get("preferred_contact")
        or "",
        "address": "",
        "dob": "",
        "call_summary": summary or "",
        "is_test_call": bool(state.allow_test_phones),
        # The old build sent a bare "en" / "es"; Deepgram is configured with
        # a locale ("en-US"). Send the two-letter code the Zap matches on.
        "language": (settings.stt.language or "en").split("-")[0],
        # The old build scored priority in services/priority_scorer.py. This
        # port has no equivalent, so the key is present and empty rather than
        # absent - an absent key breaks a Zap step that maps it.
        "priority_level": "",
    }


def build_payload(
    state: CallState,
    custom: dict[str, Any],
    summary: str = "",
) -> dict[str, Any] | None:
    """The old build's payload, or None for a case type it never handled."""
    agent_name = _AGENT_NAME_BY_CASE_TYPE.get(state.case_type or "")
    if agent_name is None:
        logger.debug("no Zapier mapping for case_type %r", state.case_type)
        return None

    started = _to_cst(datetime.fromtimestamp(state.started_at, tz=timezone.utc))
    agent_obj = {k: v for k, v in custom.items() if k not in _USER_LEVEL_FIELDS}

    return {
        "session_id": state.call_id or state.room_name or "",
        "agent_name": agent_name,
        "call_started_at": started.isoformat(),
        "user": _user_object(state, custom, summary),
        agent_name: agent_obj,
        "metadata": {
            "call_id": state.call_id or "",
            "room_name": state.room_name or "",
            "duration_ms": int(state.duration_seconds * 1000),
            "disconnection_reason": state.disconnect_reason or "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    }


async def send(
    state: CallState,
    custom: dict[str, Any],
    summary: str = "",
) -> dict[str, Any]:
    """Build and POST. Never raises - a webhook must not fail a call."""
    payload = build_payload(state, custom, summary)
    if payload is None:
        return {"status": "skipped", "reason": "unsupported case_type", "payload": None}

    url = settings.post_call.zapier_webhook_url
    if not url:
        logger.debug("ZAPIER_WEBHOOK_URL not set, skipping Zapier webhook")
        return {"status": "skipped", "reason": "ZAPIER_WEBHOOK_URL not set", "payload": payload}

    timeout = aiohttp.ClientTimeout(total=settings.post_call.webhook_timeout_ms / 1000)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.post(url, json=payload) as resp:
                body = await resp.text()
                if resp.status in (200, 201, 202):
                    logger.info("Zapier webhook delivered (%s)", resp.status)
                    return {"status": "success", "response_status": resp.status, "payload": payload}
                logger.warning("Zapier webhook returned %s: %s", resp.status, body[:400])
                return {
                    "status": "error",
                    "error": f"HTTP {resp.status}",
                    "response_status": resp.status,
                    "payload": payload,
                }
    except Exception as exc:
        logger.exception("Zapier webhook delivery failed")
        return {"status": "error", "error": str(exc), "payload": payload}
