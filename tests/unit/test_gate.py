from drx.core.gate import GateDecision, GateEngine, GateStatus


def test_gate_passes_only_when_all_evidence_passes() -> None:
    checks = {gate_id: True for gate_id in GateEngine.REQUIRED_GATES}
    evaluation = GateEngine().evaluate_gates(checks)
    assert evaluation.decision is GateDecision.BLOCKED


def test_gate_fails_on_any_failure() -> None:
    evaluation = GateEngine().evaluate_gates({"G4_TEST": False})
    assert evaluation.decision is GateDecision.FAIL


def test_gate_blocks_missing_or_incomplete_evidence() -> None:
    empty = GateEngine().evaluate_gates({})
    incomplete = GateEngine().evaluate_gates({"G4_TEST": None})
    assert empty.decision is GateDecision.BLOCKED
    assert incomplete.decision is GateDecision.BLOCKED


def test_gate_preserves_an_explicit_review_hold() -> None:
    checks = {gate_id: True for gate_id in GateEngine.REQUIRED_GATES}
    checks["G2_TASK"] = GateStatus.HOLD

    evaluation = GateEngine().evaluate_gates(checks)

    assert evaluation.decision is GateDecision.BLOCKED
    hold_result = next(
        result for result in evaluation.results if result.id == "G2_TASK"
    )
    assert hold_result.status is GateStatus.HOLD


def test_gate_rejects_unregistered_gates() -> None:
    checks = {gate_id: True for gate_id in GateEngine.REQUIRED_GATES}
    checks["G7_AGENT_ASSERTION"] = True

    evaluation = GateEngine().evaluate_gates(checks)

    assert evaluation.decision is GateDecision.BLOCKED
    assert evaluation.results[-1].id == "G7_AGENT_ASSERTION"
    assert evaluation.results[-1].status is GateStatus.BLOCKED
