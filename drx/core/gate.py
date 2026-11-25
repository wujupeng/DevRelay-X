from enum import StrEnum


class GateDecision(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"


class GateEngine:
    """Determines final status only from explicit evidence."""

    def evaluate(self, evidence_results: list[str]) -> GateDecision:
        if not evidence_results:
            return GateDecision.BLOCKED
        normalized = {result.lower() for result in evidence_results}
        if "fail" in normalized:
            return GateDecision.FAIL
        if "blocked" in normalized or "pending" in normalized:
            return GateDecision.BLOCKED
        if normalized == {"pass"}:
            return GateDecision.PASS
        return GateDecision.BLOCKED
