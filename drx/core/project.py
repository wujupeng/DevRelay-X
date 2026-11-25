from dataclasses import dataclass


@dataclass(slots=True)
class Project:
    id: str
    name: str
    repository: str
    description: str = ""
