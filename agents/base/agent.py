"""Shared behavior for the five practice-area intake agents."""

import logging

from livekit.agents import Agent, llm
from livekit.agents.beta.tools import EndCallTool

from agents.user_data import CallData
from prompts.common_prompts import (
    END_CALL_DESCRIPTION,
    END_CALL_INSTRUCTIONS,
    HANDOFF_INSTRUCTIONS,
    compose,
)

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
        super().__init__(instructions=compose(self.prompt), chat_ctx=chat_ctx, tools=[end_call])

    async def on_enter(self) -> None:
        data: CallData = self.session.userdata
        data.case_type = self.case_type
        data.agent_name = self.display_name
        logger.info("intake agent active: %s", self.case_type)
        await self.session.generate_reply(instructions=HANDOFF_INSTRUCTIONS)
