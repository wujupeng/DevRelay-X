from abc import ABC, abstractmethod
from typing import Any


class AgentAdapter(ABC):
    """Boundary for external AI coding agents."""

    @abstractmethod
    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        """Execute a task and return evidence-bearing output."""
