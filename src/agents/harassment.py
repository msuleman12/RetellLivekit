"""Sexual harassment / assault intake.

Retell equivalent: agent_2faa4e69b3d4d679bf06c52579, response engine
llm_4effc49823e1a4253f9c258673a5 v16.

Note: Retell's post-call field for this agent describes `other_party_name` as
"Do not push hard for this if the caller is distressed", and unlike the other
four agents it is not marked required. So `other_party_required` is False here
(must-have tracking / post-call only; end_call itself is Retell-style).
"""

from __future__ import annotations

from livekit.agents import NOT_GIVEN, llm

from .. import prompts
from .base import BaseIntakeAgent


class HarassmentAgent(BaseIntakeAgent):
    retell_agent_name = "Bush & Bush Law Group - Sexual Harassment"
    case_type = "harassment"
    begin_message = prompts.HARASSMENT_BEGIN_MESSAGE
    other_party_label = "the person or employer involved, only if they offer it"
    require_other_party = False

    def __init__(self, *, greet: bool = False, chat_ctx: llm.ChatContext | None = None) -> None:
        super().__init__(
            prompt=prompts.HARASSMENT_PROMPT,
            greet=greet,
            chat_ctx=chat_ctx if chat_ctx is not None else NOT_GIVEN,
        )
