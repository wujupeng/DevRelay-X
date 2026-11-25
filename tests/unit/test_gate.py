from drx.core.gate import GateDecision, GateEngine, GateStatus


def test_gate_passes_only_when_all_evidence_passes() -> None:
    decision, _ = GateEngine().evaluate_gates({"G0_ENVIRONMENT": True, "G4_TEST": True})
    assert decision is GateDecision.PASS


def test_gate_fails_on_any_failure() -> None:
    decision, _ = GateEngine().evaluate_gates({"G4_TEST": False})
    assert decision is GateDecision.FAIL


def test_gate_blocks_missing_or_incomplete_evidence() -> None:
    empty_decision, _ = GateEngine().evaluate_gates({})
    incomplete_decision, _ = GateEngine().evaluate_gates({"G4_TEST": None})
    assert empty_decision is GateDecision.BLOCKED
    assert incomplete_decision is GateDecision.BLOCKED


def test_gate_preserves_an_explicit_review_hold() -> None:
    decision, results = GateEngine().evaluate_gates({"G2_TASK": GateStatus.HOLD})
    assert decision is GateDecision.HOLD
    assert results[0].status is GateStatus.HOLD
