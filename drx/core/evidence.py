from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EvidenceType(StrEnum):
    COMMIT = "commit"
    TEST = "test"
    REVIEW = "review"
    POLICY = "policy"
    ARTIFACT = "artifact"


@dataclass(slots=True)
class Evidence:
    id: str
    task_id: str
    type: EvidenceType
    producer: str
    result: str
    metadata: dict[str, Any] = field(default_factory=dict)
