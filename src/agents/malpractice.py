"""Medical malpractice intake.

Retell equivalent: agent_fc28f007dc909ed98663bc8296, response engine
llm_ee5c08f9b7025aa4871646d8d7f0 v16.
"""

from __future__ import annotations

from livekit.agents import llm

from .. import prompts
from .base import BaseIntakeAgent


class MalpracticeAgent(BaseIntakeAgent):
    retell_agent_name = "Bush & Bush Law Group - Medical Malpractice"
    case_type = "malpractice"
    begin_message = prompts.MALPRACTICE_BEGIN_MESSAGE
    other_party_label = "the doctor, hospital, clinic or facility"
    source_prompt = prompts.MALPRACTICE_PROMPT

    def __init__(self, *, greet: bool = False, chat_ctx: llm.ChatContext | None = None) -> None:
        super().__init__(greet=greet, chat_ctx=chat_ctx)
