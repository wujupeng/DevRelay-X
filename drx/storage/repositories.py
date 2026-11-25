from typing import Protocol, TypeVar

from sqlalchemy.orm import Session

T = TypeVar("T")


class Repository(Protocol[T]):
    def get(self, session: Session, record_id: str) -> T | None: ...
    def add(self, session: Session, record: T) -> T: ...
