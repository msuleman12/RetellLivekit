"""Employment / workplace intake."""

import time

from livekit.agents import RunContext, function_tool

from agents.base.agent import BaseIntakeAgent
from agents.sexual_harassment.agent import SexualHarassmentAgent
from agents.user_data import CallData
from prompts.employment_prompts import EMPLOYMENT_PROMPT


class EmploymentAgent(BaseIntakeAgent):
    case_type = "employment"
    display_name = "Bush & Bush Law Group - Employment"
    prompt = EMPLOYMENT_PROMPT

    @function_tool()
    async def switch_to_sexual_harassment_intake(self, context: RunContext[CallData]):
        """Call this as soon as the caller says the workplace issue involves sexual
        harassment or sexual assault. Do not say anything before calling it."""
        context.userdata.handoffs.append(
            {"from": self.case_type, "to": "harassment", "at": int(time.time() * 1000)}
        )
        return SexualHarassmentAgent(chat_ctx=self.chat_ctx.copy(exclude_instructions=True))
