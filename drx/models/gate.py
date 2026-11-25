from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from drx.storage.database import Base


class GateRecord(Base):
    __tablename__ = "gate_decisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tasks.id"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
