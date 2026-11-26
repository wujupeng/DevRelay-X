from sqlalchemy.orm import Session

from drx.agent.run import AgentRun, AgentRunState
from drx.core.task import Task, TaskStatus, transition_task
from drx.models.run import RunRecord
from drx.models.task import TaskRecord


class ExecutionLifecycle:
    @staticmethod
    def transition_run(
        session: Session,
        agent_run: AgentRun,
        record: RunRecord,
        next_state: AgentRunState,
    ) -> None:
        agent_run.transition(next_state)
        record.status = agent_run.state.value
        session.commit()

    @staticmethod
    def transition_task(task_record: TaskRecord, next_status: TaskStatus) -> None:
        task = Task(
            task_record.id,
            task_record.requirement_id,
            task_record.title,
            TaskStatus(task_record.status),
        )
        transitioned = transition_task(task, next_status)
        task_record.status = transitioned.status.value
