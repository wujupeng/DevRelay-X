from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType

from drx.agent.capabilities import REGISTERED_CAPABILITIES, AgentCapabilities


@dataclass(frozen=True, slots=True)
class PolicyResult:
    allowed: bool
    reason: str


class PolicyEngine:
    """Allow only explicitly supported local execution operations."""

    ACTION_REQUIREMENTS = MappingProxyType(
        {
            "task.execute": ("task-prompting", frozenset()),
            "runtime.health_check": ("runtime-health-check", frozenset()),
            "runtime.generate": (
                "runtime-generation",
                frozenset({"G0_ENVIRONMENT", "G1_WORKSPACE", "G2_TASK"}),
            ),
            "workspace.create": ("workspace-isolation", frozenset({"G0_ENVIRONMENT"})),
            "workspace.context.read": (
                "workspace-context-read",
                frozenset({"G0_ENVIRONMENT", "G1_WORKSPACE", "G2_TASK"}),
            ),
            "workspace.apply_diff": (
                "unified-diff-only",
                frozenset({"G0_ENVIRONMENT", "G1_WORKSPACE", "G2_TASK"}),
            ),
            "tests.run": ("test-execution", frozenset({"G3_EXECUTION"})),
            "git.collect_evidence": (
                "git-evidence",
                frozenset({"G0_ENVIRONMENT", "G1_WORKSPACE", "G2_TASK"}),
            ),
            "git.commit": ("git-commit", frozenset({"G4_TEST"})),
        }
    )

    REGISTERED_ACTIONS = frozenset(ACTION_REQUIREMENTS)
    HIGH_RISK_ACTIONS = frozenset(
        {"runtime.generate", "workspace.apply_diff", "git.commit"}
    )
    DEFAULT_ALLOWED_ACTIONS = REGISTERED_ACTIONS - HIGH_RISK_ACTIONS

    def __init__(self, allowed_actions: Iterable[str] | None = None) -> None:
        configured = (
            self.DEFAULT_ALLOWED_ACTIONS
            if allowed_actions is None
            else frozenset(allowed_actions)
        )
        unsupported = configured - self.REGISTERED_ACTIONS
        if unsupported:
            raise ValueError(f"unsupported policy actions: {', '.join(sorted(unsupported))}")
        self.allowed_actions = frozenset(configured)

    def evaluate(
        self,
        action: str,
        *,
        capabilities: AgentCapabilities | Iterable[str] | None = None,
        passed_gates: Iterable[str] = (),
    ) -> PolicyResult:
        if not isinstance(action, str) or not action.strip():
            return PolicyResult(False, "action is empty")
        if action not in self.allowed_actions:
            return PolicyResult(False, "action is not explicitly allowed")
        requirements = self.ACTION_REQUIREMENTS.get(action)
        if requirements is None:
            return PolicyResult(False, "action has no registered capability policy")
        if capabilities is None:
            return PolicyResult(False, "agent capabilities are unavailable")
        capability_ids = (
            set(capabilities.ids())
            if isinstance(capabilities, AgentCapabilities)
            else set(capabilities)
        )
        unregistered = capability_ids - REGISTERED_CAPABILITIES
        if unregistered:
            return PolicyResult(
                False,
                f"agent has unregistered capabilities: {', '.join(sorted(unregistered))}",
            )
        required_capability, required_gates = requirements
        if required_capability not in capability_ids:
            return PolicyResult(
                False, f"required capability '{required_capability}' is unavailable"
            )
        missing_gates = required_gates - set(passed_gates)
        if missing_gates:
            return PolicyResult(
                False,
                "required gates have not passed: "
                + ", ".join(sorted(missing_gates)),
            )
        return PolicyResult(True, "action is explicitly allowed")
