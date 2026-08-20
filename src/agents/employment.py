"""Employment / workplace intake.

Retell equivalent: agent_df71d82842c2299cc238be6819, response engine
llm_447f6d61d2f78869b558b94474ff v16.
"""

from __future__ import annotations

from livekit.agents import NOT_GIVEN, llm

from .. import prompts
from .base import BaseIntakeAgent


class EmploymentAgent(BaseIntakeAgent):
    retell_agent_name = "Bush & Bush Law Group - Employment"
    case_type = "employment"
    begin_message = prompts.EMPLOYMENT_BEGIN_MESSAGE
    other_party_label = "the employer or company"

    def __init__(self, *, greet: bool = False, chat_ctx: llm.ChatContext | None = None) -> None:
        super().__init__(
            prompt=prompts.EMPLOYMENT_PROMPT,
            greet=greet,
            chat_ctx=chat_ctx if chat_ctx is not None else NOT_GIVEN,
        )
