from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from drx.storage.database import Base


class RunRecord(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tasks.id"), nullable=False
    )
    agent_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("agents.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="started")
    runtime_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    workspace: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    initial_commit: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    final_commit: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    failure_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
