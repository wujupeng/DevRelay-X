from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentCapability:
    id: str
    description: str


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    capabilities: tuple[AgentCapability, ...]

    def supports(self, capability_id: str) -> bool:
        return any(capability.id == capability_id for capability in self.capabilities)

    def ids(self) -> tuple[str, ...]:
        return tuple(capability.id for capability in self.capabilities)
