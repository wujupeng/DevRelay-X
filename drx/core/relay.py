from dataclasses import dataclass
from enum import StrEnum


class RelayStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    ACTIVE = "active"
    COMPLETED = "completed"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(slots=True)
class Relay:
    id: str
    task_id: str
    from_agent_id: str | None
    to_agent_id: str
    status: RelayStatus = RelayStatus.PENDING
