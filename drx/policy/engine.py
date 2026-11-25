from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PolicyResult:
    allowed: bool
    reason: str


class PolicyEngine:
    """Foundation policy boundary. Add explicit policy rules before enforcing delivery."""

    def evaluate(self, action: str) -> PolicyResult:
        if not action.strip():
            return PolicyResult(False, "action is empty")
        return PolicyResult(True, "foundation policy allows the action")
