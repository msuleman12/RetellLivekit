"""Router: greets the caller and hands off to the right practice-area agent."""

import logging
import time
from typing import Literal

from livekit.agents import Agent, RunContext, function_tool
from livekit.agents.beta.tools import EndCallTool

from agents.accident.agent import AccidentAgent
from agents.base.agent import BaseIntakeAgent
from agents.employment.agent import EmploymentAgent
from agents.medical_malpractice.agent import MedicalMalpracticeAgent
from agents.premises_liability.agent import PremisesLiabilityAgent
from agents.sexual_harassment.agent import SexualHarassmentAgent
from agents.user_data import CallData
from config import OPENAI
from prompts.router_prompts import DECLINE_INSTRUCTIONS, ROUTER_GREETING, ROUTER_INSTRUCTIONS
from services.voice_pipeline import build_llm

logger = logging.getLogger("intake.router")

CaseType = Literal["accident", "employment", "premises", "harassment", "malpractice"]

AGENTS_BY_CASE_TYPE: dict[str, type[BaseIntakeAgent]] = {
    "accident": AccidentAgent,
    "employment": EmploymentAgent,
    "premises": PremisesLiabilityAgent,
    "harassment": SexualHarassmentAgent,
    "malpractice": MedicalMalpracticeAgent,
}


class RouterAgent(Agent):
    display_name = "Bush & Bush Law Group - Router"

    def __init__(self) -> None:
        decline = EndCallTool(
            extra_description=(
                "Use ONLY in two cases: (1) a clarifying question has been asked and "
                "answered and the matter is clearly none of the five practice areas, or "
                "(2) an existing client gave their name and callback number and signed off."
            ),
            end_instructions=DECLINE_INSTRUCTIONS,
            delete_room=True,
        )
        super().__init__(
            instructions=ROUTER_INSTRUCTIONS,
            llm=build_llm(OPENAI.router_model, OPENAI.router_temperature),
            tools=[decline],
        )

    async def on_enter(self) -> None:
        self.session.userdata.agent_name = self.display_name
        await self.session.say(ROUTER_GREETING)

    @function_tool()
    async def route_call(self, context: RunContext[CallData], case_type: CaseType):
        """Hand the caller to the intake specialist for their matter, as soon as the
        category is clear. Call it silently: say nothing before or after, and never
        mention routing, connecting, transferring or holding.

        Args:
            case_type: accident, employment, premises, harassment or malpractice.
        """
        context.userdata.handoffs.append({"to": case_type, "at": int(time.time() * 1000)})
        logger.info("routing call to %s", case_type)
        agent_cls = AGENTS_BY_CASE_TYPE[case_type]
        return agent_cls(chat_ctx=self.chat_ctx.copy(exclude_instructions=True))
