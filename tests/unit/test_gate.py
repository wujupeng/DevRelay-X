from drx.core.gate import GateDecision, GateEngine


def test_gate_passes_only_when_all_evidence_passes() -> None:
    assert GateEngine().evaluate(["pass", "pass"]) is GateDecision.PASS


def test_gate_fails_on_any_failure() -> None:
    assert GateEngine().evaluate(["pass", "fail"]) is GateDecision.FAIL


def test_gate_blocks_missing_or_incomplete_evidence() -> None:
    assert GateEngine().evaluate([]) is GateDecision.BLOCKED
    assert GateEngine().evaluate(["pending"]) is GateDecision.BLOCKED
