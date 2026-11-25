from dataclasses import dataclass, field
from enum import StrEnum


class RequirementStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    DECOMPOSED = "decomposed"
    CLOSED = "closed"


@dataclass(slots=True)
class Requirement:
    id: str
    project_id: str
    title: str
    description: str = ""
    status: RequirementStatus = RequirementStatus.DRAFT
    acceptance_criteria: list[str] = field(default_factory=list)
