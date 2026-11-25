from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class AgentRunState(StrEnum):
    CREATED = "created"
    PREPARING = "preparing"
    RUNNING = "running"
    CHANGED = "changed"
    TESTING = "testing"
    TEST_PASS = "test_pass"
    TEST_FAIL = "test_fail"
    EVIDENCE_COLLECTED = "evidence_collected"
    GATE_PENDING = "gate_pending"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


RUN_TRANSITIONS: dict[AgentRunState, frozenset[AgentRunState]] = {
    AgentRunState.CREATED: frozenset({AgentRunState.PREPARING, AgentRunState.CANCELLED}),
    AgentRunState.PREPARING: frozenset(
        {AgentRunState.RUNNING, AgentRunState.BLOCKED, AgentRunState.FAILED}
    ),
    AgentRunState.RUNNING: frozenset(
        {AgentRunState.CHANGED, AgentRunState.BLOCKED, AgentRunState.FAILED}
    ),
    AgentRunState.CHANGED: frozenset(
        {AgentRunState.TESTING, AgentRunState.BLOCKED, AgentRunState.FAILED}
    ),
    AgentRunState.TESTING: frozenset(
        {
            AgentRunState.TEST_PASS,
            AgentRunState.TEST_FAIL,
            AgentRunState.BLOCKED,
            AgentRunState.FAILED,
        }
    ),
    AgentRunState.TEST_PASS: frozenset(
        {AgentRunState.EVIDENCE_COLLECTED, AgentRunState.BLOCKED, AgentRunState.FAILED}
    ),
    AgentRunState.TEST_FAIL: frozenset(
        {AgentRunState.EVIDENCE_COLLECTED, AgentRunState.BLOCKED, AgentRunState.FAILED}
    ),
    AgentRunState.EVIDENCE_COLLECTED: frozenset({AgentRunState.GATE_PENDING}),
    AgentRunState.GATE_PENDING: frozenset(
        {AgentRunState.COMPLETED, AgentRunState.BLOCKED, AgentRunState.FAILED}
    ),
    AgentRunState.COMPLETED: frozenset(),
    AgentRunState.FAILED: frozenset(),
    AgentRunState.BLOCKED: frozenset(),
    AgentRunState.CANCELLED: frozenset(),
}


@dataclass(slots=True)
class AgentRun:
    id: str
    task_id: str
    agent_id: str
    runtime_id: str
    state: AgentRunState = AgentRunState.CREATED
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    transitions: list[str] = field(default_factory=lambda: [AgentRunState.CREATED.value])
    failure_reason: str = ""

    def transition(self, next_state: AgentRunState) -> None:
        if next_state not in RUN_TRANSITIONS[self.state]:
            raise ValueError(f"illegal AgentRun transition: {self.state} -> {next_state}")
        self.state = next_state
        self.transitions.append(next_state.value)


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    run_id: str
    state: AgentRunState
    gate_decision: str
    evidence: dict[str, object]
