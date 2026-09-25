"""Forwarding a caller to a human, by SIP REFER.

A REFER does not complete until the far end answers, so a failed or unanswered
transfer leaves the caller exactly where they were - still on the line with the
agent. Every caller in this file therefore treats False as "carry on with the
intake", never as "the call is over".
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from livekit import api, rtc
from livekit.agents import AgentSession, get_job_context

from agents.user_data import CallData
from config import TRANSFER
from utils.phone import mask_phone

logger = logging.getLogger("intake.transfer")

# The codes worth reading in the logs. Anything else is logged as-is.
SIP_STATUS_MEANING = {
    401: "trunk authentication failed",
    403: "trunk refused the transfer - check SIP REFER is enabled",
    404: "number or trunk not found",
    408: "timed out reaching the trunk",
    480: "line unavailable",
    486: "line busy",
    487: "cancelled - the caller hung up during the transfer",
    488: "codec mismatch on the trunk",
    500: "trunk internal error",
    503: "trunk unavailable",
    603: "the call was declined",
}

# Codes that mean "nobody picked up", as opposed to "our trunk is misconfigured".
# The difference decides whether the firm should be chasing missed calls or a setup
# problem, so the record keeps them apart.
NO_ANSWER_CODES = frozenset({408, 480, 486, 487, 600, 603})


@dataclass(frozen=True)
class TransferResult:
    """What became of a REFER.

    Truthy only when the caller was actually handed over, so an existing
    "if await transfer_to_number(...)" check keeps reading the same way.
    """

    connected: bool
    outcome: str
    sip_code: int | None = None

    def __bool__(self) -> bool:
        return self.connected


def _sip_caller(room: rtc.Room) -> rtc.RemoteParticipant | None:
    """The phone caller in the room. The identity is assigned at dispatch and
    is not the phone number, so it has to be looked up by participant kind."""
    return next(
        (
            p
            for p in room.remote_participants.values()
            if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
        ),
        None,
    )


async def transfer_to_number(phone_number: str) -> TransferResult:
    """Forward the caller to a phone number. A falsy result means they are still
    on the line, and the outcome says why."""
    if not phone_number:
        logger.error("no number configured for this transfer")
        return TransferResult(False, "not_configured")

    job_ctx = get_job_context()
    caller = _sip_caller(job_ctx.room)
    if caller is None:
        logger.error("no SIP caller in the room to transfer")
        return TransferResult(False, "no_sip_caller")

    number = phone_number.strip()
    if not number.startswith("+"):
        number = f"+{number}"

    request = api.TransferSIPParticipantRequest(
        room_name=job_ctx.room.name,
        participant_identity=caller.identity,
        transfer_to=f"tel:{number}",
        # A dial tone would sound as if the caller had dialled out.
        play_dialtone=False,
    )
    # A Duration, not a number of seconds - it has to be filled in afterwards.
    request.ringing_timeout.FromTimedelta(timedelta(seconds=TRANSFER.ringing_timeout_s))

    try:
        await job_ctx.api.sip.transfer_sip_participant(request)
    except api.SipCallError as exc:
        code = exc.sip_status_code
        meaning = SIP_STATUS_MEANING.get(code, exc.sip_status)
        logger.warning("transfer failed: SIP %s - %s", code, meaning)
        outcome = "no_answer" if code in NO_ANSWER_CODES else "trunk_error"
        return TransferResult(False, outcome, code)
    except Exception:
        logger.exception("transfer failed")
        return TransferResult(False, "error")

    logger.info("caller transferred to %s", mask_phone(number))
    return TransferResult(True, "connected")


async def hand_off_to_human(
    session: AgentSession,
    data: CallData,
    *,
    number: str,
    target: str,
    reason: str,
    line: str,
) -> bool:
    """Say one line, let it finish, then forward the caller. Records the attempt.

    False means the caller is still on the line and the intake must continue.
    """
    data.transfer_requested = True
    data.transfer_attempted = True
    data.transfer_target = target
    data.transfer_reason = reason
    data.transfer_destination = number
    logger.info("transferring to %s (%s)", target, reason)

    # Waiting on the handle, rather than sleeping a fixed two seconds, means the
    # REFER goes out the moment the sentence ends - no dead air, nothing clipped.
    await session.say(line, allow_interruptions=False)

    result = await transfer_to_number(number)
    data.transfer_succeeded = result.connected
    data.transfer_sip_code = result.sip_code
    data.transfer_outcome = result.outcome
    return result.connected


def record_declined_transfer(data: CallData, *, target: str, reason: str) -> None:
    """A human was asked for but could not be offered, so the AI carries on alone.

    No REFER goes out here, so transfer_attempted stays False - but the ask still
    has to reach the call record, or "the caller wanted a person and we said no"
    is invisible to the firm afterwards.
    """
    data.transfer_requested = True
    data.transfer_target = target
    data.transfer_reason = reason
    data.transfer_outcome = f"declined_{reason}"
    logger.info("%s transfer declined (%s)", target, reason)
