from sqlalchemy import ForeignKey, String
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
