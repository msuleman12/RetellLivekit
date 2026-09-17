"""Car accident / general intake."""

from agents.base.agent import BaseIntakeAgent
from prompts.accident_prompts import ACCIDENT_PROMPT


class AccidentAgent(BaseIntakeAgent):
    case_type = "accident"
    display_name = "Bush & Bush Law Group - Intake"
    prompt = ACCIDENT_PROMPT
