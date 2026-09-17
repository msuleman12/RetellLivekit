"""Sexual harassment / assault intake."""

from agents.base.agent import BaseIntakeAgent
from prompts.sexual_harassment_prompts import HARASSMENT_PROMPT


class SexualHarassmentAgent(BaseIntakeAgent):
    case_type = "harassment"
    display_name = "Bush & Bush Law Group - Sexual Harassment"
    prompt = HARASSMENT_PROMPT
