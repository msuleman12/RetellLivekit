"""Premises liability (slip and fall) intake."""

from agents.base.agent import BaseIntakeAgent
from prompts.premises_liability_prompts import PREMISES_PROMPT


class PremisesLiabilityAgent(BaseIntakeAgent):
    case_type = "premises"
    display_name = "Bush & Bush Law Group - Premises Liability"
    prompt = PREMISES_PROMPT
