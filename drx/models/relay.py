from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from drx.storage.database import Base


class RelayRecord(Base):
    __tablename__ = "relays"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tasks.id"), nullable=False
    )
    from_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_agent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
