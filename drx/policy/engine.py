from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PolicyResult:
    allowed: bool
    reason: str


class PolicyEngine:
    """Allow only explicitly supported local execution operations."""

    DEFAULT_ALLOWED_ACTIONS = frozenset(
        {
            "task.execute",
            "runtime.health_check",
            "runtime.generate",
            "workspace.create",
            "workspace.context.read",
            "workspace.apply_diff",
            "tests.run",
            "git.collect_evidence",
            "git.commit",
        }
    )

    def __init__(self, allowed_actions: Iterable[str] | None = None) -> None:
        configured = (
            self.DEFAULT_ALLOWED_ACTIONS
            if allowed_actions is None
            else frozenset(allowed_actions)
        )
        unsupported = configured - self.DEFAULT_ALLOWED_ACTIONS
        if unsupported:
            raise ValueError(f"unsupported policy actions: {', '.join(sorted(unsupported))}")
        self.allowed_actions = frozenset(configured)

    def evaluate(self, action: str) -> PolicyResult:
        if not action.strip():
            return PolicyResult(False, "action is empty")
        if action not in self.allowed_actions:
            return PolicyResult(False, "action is not explicitly allowed")
        return PolicyResult(True, "action is explicitly allowed")
