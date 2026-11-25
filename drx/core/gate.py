from dataclasses import dataclass
from enum import StrEnum


class GateDecision(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"
    HOLD = "hold"


class GateStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"
    HOLD = "hold"


@dataclass(frozen=True, slots=True)
class GateResult:
    id: str
    status: GateStatus
    reason: str


class GateEngine:
    """Determines final status only from explicit evidence."""

    def evaluate_gates(
        self, checks: dict[str, bool | None | GateStatus]
    ) -> tuple[GateDecision, tuple[GateResult, ...]]:
        results: list[GateResult] = []
        for gate_id, verified in checks.items():
            if verified == GateStatus.HOLD:
                status = GateStatus.HOLD
                reason = "explicit review hold"
            elif verified is True:
                status = GateStatus.PASS
                reason = "required evidence verified"
            elif verified is False:
                status = GateStatus.FAIL
                reason = "required evidence was verified and failed"
            else:
                status = GateStatus.BLOCKED
                reason = "required evidence is unavailable or unverified"
            results.append(GateResult(gate_id, status, reason))

        statuses = {result.status for result in results}
        if GateStatus.FAIL in statuses:
            decision = GateDecision.FAIL
        elif GateStatus.BLOCKED in statuses:
            decision = GateDecision.BLOCKED
        elif GateStatus.HOLD in statuses:
            decision = GateDecision.HOLD
        elif statuses == {GateStatus.PASS}:
            decision = GateDecision.PASS
        else:
            decision = GateDecision.BLOCKED
        return decision, tuple(results)
