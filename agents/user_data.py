"""Per-call state, available as `session.userdata`."""

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CallData:
    call_id: str
    room_name: str
    from_number: str = ""
    to_number: str = ""
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    case_type: str = ""
    agent_name: str = ""
    handoffs: list[dict[str, Any]] = field(default_factory=list)
    disconnect_reason: str = ""

    @property
    def duration_ms(self) -> int:
        return int(((self.ended_at or time.time()) - self.started_at) * 1000)

    def end(self, reason: str) -> None:
        if not self.disconnect_reason:
            self.disconnect_reason = reason
        if self.ended_at is None:
            self.ended_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "room_name": self.room_name,
            "from_number": self.from_number,
            "to_number": self.to_number,
            "start_timestamp": int(self.started_at * 1000),
            "end_timestamp": int((self.ended_at or time.time()) * 1000),
            "duration_ms": self.duration_ms,
            "case_type": self.case_type,
            "agent_name": self.agent_name,
            "handoffs": self.handoffs,
            "disconnection_reason": self.disconnect_reason,
        }
