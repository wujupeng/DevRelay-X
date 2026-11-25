import pytest

from drx.agent.run import AgentRun, AgentRunState
from drx.core.task import Task, TaskStatus, transition_task


def test_agent_run_rejects_illegal_state_transition() -> None:
    run = AgentRun("run", "task", "agent", "runtime")
    with pytest.raises(ValueError, match="illegal AgentRun transition"):
        run.transition(AgentRunState.COMPLETED)


def test_task_cannot_pass_before_verification() -> None:
    task = Task("task", "requirement", "title")
    with pytest.raises(ValueError, match="illegal task transition"):
        transition_task(task, TaskStatus.PASS)
