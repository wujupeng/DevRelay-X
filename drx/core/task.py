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


TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.CREATED: frozenset(
        {TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED, TaskStatus.CANCELLED}
    ),
    TaskStatus.ASSIGNED: frozenset(
        {TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED, TaskStatus.CANCELLED}
    ),
    TaskStatus.IN_PROGRESS: frozenset(
        {TaskStatus.IMPLEMENTED, TaskStatus.FAIL, TaskStatus.BLOCKED, TaskStatus.CANCELLED}
    ),
    TaskStatus.IMPLEMENTED: frozenset(
        {TaskStatus.VERIFYING, TaskStatus.FAIL, TaskStatus.BLOCKED}
    ),
    TaskStatus.VERIFYING: frozenset(
        {TaskStatus.PASS, TaskStatus.FAIL, TaskStatus.BLOCKED}
    ),
    TaskStatus.PASS: frozenset(),
    TaskStatus.FAIL: frozenset(),
    TaskStatus.BLOCKED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


def transition_task(task: Task, next_status: TaskStatus) -> None:
    if next_status not in TASK_TRANSITIONS[task.status]:
        raise ValueError(f"illegal task transition: {task.status} -> {next_status}")
    task.status = next_status
