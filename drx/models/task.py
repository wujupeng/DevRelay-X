from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, validates

from drx.core.task import (
    TASK_TRANSITIONS,
    TaskStatus,
    _is_gate_pass_authorized,
)
from drx.storage.database import Base


class TaskRecord(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    requirement_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("requirements.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    objective: Mapped[str] = mapped_column(Text, nullable=False, default="")
    acceptance_criteria_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    @validates("status")
    def validate_status(self, _key: str, value: str) -> str:
        next_status = TaskStatus(value)
        current_value = self.__dict__.get("status")
        if current_value is None:
            if next_status != TaskStatus.CREATED:
                raise ValueError("a Task must start in the created state")
            return next_status.value

        current_status = TaskStatus(current_value)
        if next_status == current_status:
            return next_status.value
        if next_status == TaskStatus.PASS:
            if (
                current_status == TaskStatus.VERIFYING
                and _is_gate_pass_authorized(self.id)
            ):
                return next_status.value
            raise ValueError("Task PASS requires verified Gate evidence")
        if next_status not in TASK_TRANSITIONS[current_status]:
            raise ValueError(
                f"illegal task transition: {current_status} -> {next_status}"
            )
        return next_status.value
