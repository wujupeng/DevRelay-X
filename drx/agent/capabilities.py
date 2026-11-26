from dataclasses import dataclass

REGISTERED_CAPABILITIES = frozenset(
    {
        "task-prompting",
        "runtime-health-check",
        "runtime-generation",
        "workspace-isolation",
        "workspace-context-read",
        "unified-diff-only",
        "test-execution",
        "git-evidence",
        "git-commit",
    }
)


@dataclass(frozen=True, slots=True)
class AgentCapability:
    id: str
    description: str


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    capabilities: tuple[AgentCapability, ...]

    def __post_init__(self) -> None:
        identifiers = tuple(capability.id for capability in self.capabilities)
        if any(not identifier.strip() for identifier in identifiers):
            raise ValueError("agent capability IDs must not be empty")
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("agent capability IDs must be unique")
        unregistered = set(identifiers) - REGISTERED_CAPABILITIES
        if unregistered:
            raise ValueError(
                f"unregistered agent capabilities: {', '.join(sorted(unregistered))}"
            )

    def supports(self, capability_id: str) -> bool:
        return any(capability.id == capability_id for capability in self.capabilities)

    def ids(self) -> tuple[str, ...]:
        return tuple(capability.id for capability in self.capabilities)
