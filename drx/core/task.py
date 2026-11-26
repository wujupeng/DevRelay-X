from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


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


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    requirement_id: str
    title: str
    status: TaskStatus = TaskStatus.CREATED


TASK_TRANSITIONS = MappingProxyType(
    {
        TaskStatus.CREATED: frozenset(
            {
                TaskStatus.ASSIGNED,
                TaskStatus.IN_PROGRESS,
                TaskStatus.BLOCKED,
                TaskStatus.CANCELLED,
            }
        ),
        TaskStatus.ASSIGNED: frozenset(
            {TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED, TaskStatus.CANCELLED}
        ),
        TaskStatus.IN_PROGRESS: frozenset(
            {
                TaskStatus.IMPLEMENTED,
                TaskStatus.FAIL,
                TaskStatus.BLOCKED,
                TaskStatus.CANCELLED,
            }
        ),
        TaskStatus.IMPLEMENTED: frozenset(
            {TaskStatus.VERIFYING, TaskStatus.FAIL, TaskStatus.BLOCKED}
        ),
        TaskStatus.VERIFYING: frozenset(
            {TaskStatus.REVIEWING, TaskStatus.FAIL, TaskStatus.BLOCKED}
        ),
        TaskStatus.REVIEWING: frozenset(
            {TaskStatus.VERIFYING, TaskStatus.FAIL, TaskStatus.BLOCKED}
        ),
        TaskStatus.PASS: frozenset(),
        TaskStatus.FAIL: frozenset(),
        TaskStatus.BLOCKED: frozenset(),
        TaskStatus.CANCELLED: frozenset(),
    }
)


_GATE_PASS_AUTHORITY = object()
_AUTHORIZED_GATE_PASS: ContextVar[str | None] = ContextVar(
    "authorized_gate_pass", default=None
)


@contextmanager
def _gate_pass_scope(task_id: str, authority: object) -> Iterator[None]:
    if authority is not _GATE_PASS_AUTHORITY:
        raise PermissionError("only the Gate Engine may authorize Task PASS")
    token = _AUTHORIZED_GATE_PASS.set(task_id)
    try:
        yield
    finally:
        _AUTHORIZED_GATE_PASS.reset(token)


def _is_gate_pass_authorized(task_id: str) -> bool:
    return _AUTHORIZED_GATE_PASS.get() == task_id


def transition_task(task: Task, next_status: TaskStatus) -> Task:
    if next_status == TaskStatus.PASS:
        raise ValueError("Task PASS can only be set by the Gate Engine")
    if next_status not in TASK_TRANSITIONS[task.status]:
        raise ValueError(f"illegal task transition: {task.status} -> {next_status}")
    return Task(task.id, task.requirement_id, task.title, next_status)
