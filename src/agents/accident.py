"""Car accident / general intake.

Retell equivalent: agent_c6413efd5341ccd76adff20485
("Bush & Bush Law Group - Intake"), response engine
llm_8958198a4d30743b61f6340e2396 v26.
"""

from __future__ import annotations

from livekit.agents import llm

from .. import prompts
from .base import BaseIntakeAgent


class AccidentAgent(BaseIntakeAgent):
    retell_agent_name = "Bush & Bush Law Group - Intake"
    case_type = "accident"
    begin_message = prompts.ACCIDENT_BEGIN_MESSAGE
    other_party_label = "the other driver, or the other party"
    source_prompt = prompts.ACCIDENT_PROMPT

    def __init__(self, *, greet: bool = False, chat_ctx: llm.ChatContext | None = None) -> None:
        super().__init__(greet=greet, chat_ctx=chat_ctx)
