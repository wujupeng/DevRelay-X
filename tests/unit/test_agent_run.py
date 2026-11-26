from dataclasses import FrozenInstanceError

import pytest

from drx.agent.run import AgentRun, AgentRunState
from drx.core.task import Task, TaskStatus, transition_task
from drx.models.task import TaskRecord


def test_agent_run_rejects_illegal_state_transition() -> None:
    run = AgentRun("run", "task", "agent", "runtime")
    with pytest.raises(ValueError, match="illegal AgentRun transition"):
        run.transition(AgentRunState.COMPLETED)


def test_task_cannot_pass_before_verification() -> None:
    task = Task("task", "requirement", "title")
    with pytest.raises(ValueError, match="only be set by the Gate Engine"):
        transition_task(task, TaskStatus.PASS)


def test_review_state_must_return_to_verification_before_gate() -> None:
    task = Task("task", "requirement", "title", TaskStatus.VERIFYING)

    reviewing = transition_task(task, TaskStatus.REVIEWING)
    resumed = transition_task(reviewing, TaskStatus.VERIFYING)

    assert reviewing.status is TaskStatus.REVIEWING
    assert resumed.status is TaskStatus.VERIFYING


def test_task_state_object_cannot_be_mutated_directly() -> None:
    task = Task("task", "requirement", "title")
    with pytest.raises(FrozenInstanceError):
        task.status = TaskStatus.PASS  # type: ignore[misc]


def test_orm_task_status_rejects_illegal_and_unverified_pass() -> None:
    task = TaskRecord(
        id="task",
        requirement_id="requirement",
        title="title",
        status=TaskStatus.CREATED.value,
    )

    with pytest.raises(ValueError, match="illegal task transition"):
        task.status = TaskStatus.IMPLEMENTED.value

    task.status = TaskStatus.IN_PROGRESS.value
    task.status = TaskStatus.IMPLEMENTED.value
    task.status = TaskStatus.VERIFYING.value
    with pytest.raises(ValueError, match="requires verified Gate evidence"):
        task.status = TaskStatus.PASS.value
