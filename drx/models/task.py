from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

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
