from dataclasses import dataclass
from enum import StrEnum


class TaskStatus(StrEnum):
    CREATED = "created"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    IMPLEMENTED = "implemented"
    VERIFYING = "verifying"
    REVIEWING = "reviewing"
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class Task:
    id: str
    requirement_id: str
    title: str
    status: TaskStatus = TaskStatus.CREATED
