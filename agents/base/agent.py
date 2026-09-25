"""Shared behavior for the five practice-area intake agents."""

import logging

from livekit.agents import Agent, RunContext, function_tool, llm
from livekit.agents.beta.tools import EndCallTool

from agents.user_data import CallData
from config import TRANSFER
from prompts.common_prompts import (
    ATTORNEY_TRANSFER_LINE,
    END_CALL_DESCRIPTION,
    END_CALL_INSTRUCTIONS,
    HANDOFF_INSTRUCTIONS,
    RELATIVE_DATES_BLOCK,
    TRANSFER_FAILED_INSTRUCTIONS,
    TRANSFERS_CLOSED_BLOCK,
    TRANSFERS_OPEN_BLOCK,
    compose,
)
from services.call_transfer import hand_off_to_human, record_declined_transfer
from utils.dates import spoken_date
from utils.office_hours import can_transfer_now, is_office_hours, transfer_denial_message

logger = logging.getLogger("intake.agent")


class BaseIntakeAgent(Agent):
    case_type: str
    display_name: str
    prompt: str

    def __init__(self, chat_ctx: llm.ChatContext) -> None:
        end_call = EndCallTool(
            extra_description=END_CALL_DESCRIPTION,
            end_instructions=END_CALL_INSTRUCTIONS,
            delete_room=True,
            # The takeover turn must speak, never hang up.
            ignore_on_enter=True,
        )
        transfers = TRANSFERS_OPEN_BLOCK if is_office_hours() else TRANSFERS_CLOSED_BLOCK
        instructions = "\n\n".join(
            [
                compose(self.prompt),
                RELATIVE_DATES_BLOCK.format(today=spoken_date()),
                transfers,
            ]
        )
        super().__init__(instructions=instructions, chat_ctx=chat_ctx, tools=[end_call])

    async def on_enter(self) -> None:
        data: CallData = self.session.userdata
        data.case_type = self.case_type
        data.agent_name = self.display_name
        logger.info("intake agent active: %s", self.case_type)

        # Urgent matters go to an attorney during office hours, before any
        # intake questions. A transfer that connects ends the session here.
        may_transfer, reason = can_transfer_now(self.case_type)
        if TRANSFER.enabled and may_transfer and not data.transfer_attempted:
            connected = await hand_off_to_human(
                self.session,
                data,
                number=TRANSFER.attorney_line,
                target="attorney",
                reason=reason,
                line=ATTORNEY_TRANSFER_LINE,
            )
            if connected:
                return
            # Nobody picked up: the caller is still here, so take the intake.
            await self.session.generate_reply(instructions=TRANSFER_FAILED_INSTRUCTIONS)
            return

        await self.session.generate_reply(instructions=HANDOFF_INSTRUCTIONS)

    @function_tool()
    async def request_attorney(self, context: RunContext[CallData]) -> str:
        """The caller has asked to speak to an attorney or a person. Call it only
        when they ask - never offer. Say nothing before or after: this tool
        speaks for itself.
        """
        data = context.userdata
        may_transfer, reason = can_transfer_now(self.case_type, user_requested=True)

        if not may_transfer or not TRANSFER.enabled:
            # When policy allows it, the only way through here is the master switch.
            record_declined_transfer(
                data,
                target="attorney",
                reason=reason if not may_transfer else "transfers_disabled",
            )
            await self.session.say(transfer_denial_message(self.case_type))
            return (
                "Attorneys cannot be reached right now and the caller has been told. "
                "Continue the intake and never offer a transfer again."
            )

        if await hand_off_to_human(
            self.session,
            data,
            number=TRANSFER.attorney_line,
            target="attorney",
            reason=reason,
            line=ATTORNEY_TRANSFER_LINE,
        ):
            return "The caller is being connected to an attorney. Say nothing further."

        await self.session.generate_reply(instructions=TRANSFER_FAILED_INSTRUCTIONS)
        return "Nobody picked up. Continue the intake and do not offer a transfer again."
