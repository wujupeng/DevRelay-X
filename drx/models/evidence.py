from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from drx.storage.database import Base


class EvidenceRecord(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tasks.id"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("runs.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    producer: Mapped[str] = mapped_column(String(200), nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
