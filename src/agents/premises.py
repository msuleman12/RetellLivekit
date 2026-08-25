"""Premises liability (slip and fall) intake.

Retell equivalent: agent_75f4b247f8be5eb0f33d723960, response engine
llm_8957323752f9aca0c393c2f3146b v16.
"""

from __future__ import annotations

from livekit.agents import llm

from .. import prompts
from .base import BaseIntakeAgent


class PremisesAgent(BaseIntakeAgent):
    retell_agent_name = "Bush & Bush Law Group - Premises Liability"
    case_type = "premises"
    begin_message = prompts.PREMISES_BEGIN_MESSAGE
    other_party_label = "the property or business where it happened"
    source_prompt = prompts.PREMISES_PROMPT

    def __init__(self, *, greet: bool = False, chat_ctx: llm.ChatContext | None = None) -> None:
        super().__init__(greet=greet, chat_ctx=chat_ctx)
