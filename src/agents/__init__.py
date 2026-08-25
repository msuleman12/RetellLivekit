"""The five Bush & Bush intake agents plus the router."""

from .accident import AccidentAgent
from .base import BaseIntakeAgent, hangup
from .employment import EmploymentAgent
from .harassment import HarassmentAgent
from .malpractice import MalpracticeAgent
from .premises import PremisesAgent
from .router import AGENTS_BY_CASE_TYPE
from .router import RETELL_AGENT_NAME as ROUTER_AGENT_NAME
from .router import RouterAgent

__all__ = [
    "AccidentAgent",
    "AGENTS_BY_CASE_TYPE",
    "BaseIntakeAgent",
    "EmploymentAgent",
    "HarassmentAgent",
    "MalpracticeAgent",
    "PremisesAgent",
    "ROUTER_AGENT_NAME",
    "RouterAgent",
    "hangup",
]
