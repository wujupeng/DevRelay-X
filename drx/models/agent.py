from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from drx.storage.database import Base


class AgentRecord(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    adapter_type: Mapped[str] = mapped_column(String(64), nullable=False)
