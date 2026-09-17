"""Medical malpractice intake."""

from agents.base.agent import BaseIntakeAgent
from prompts.medical_malpractice_prompts import MALPRACTICE_PROMPT


class MedicalMalpracticeAgent(BaseIntakeAgent):
    case_type = "malpractice"
    display_name = "Bush & Bush Law Group - Medical Malpractice"
    prompt = MALPRACTICE_PROMPT
