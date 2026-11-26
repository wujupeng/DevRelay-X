from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from drx.core.gate_authority import _issue_gate_approval
from drx.core.task import _GATE_PASS_AUTHORITY, _gate_pass_scope
from drx.evidence.verifier import EvidenceVerifier, VerifiedRunEvidence

if TYPE_CHECKING:
    from drx.models.evidence import EvidenceRecord
    from drx.models.run import RunRecord
    from drx.models.task import TaskRecord


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


@dataclass(frozen=True, slots=True)
class _GateApproval:
    task_id: str
    run_id: str
    agent_id: str
    evidence_digest: str
    authority: object


@dataclass(frozen=True, slots=True)
class GateEvaluation:
    decision: GateDecision
    results: tuple[GateResult, ...]
    evidence: VerifiedRunEvidence | None
    approval: _GateApproval | None

    def __iter__(self) -> Iterator[GateDecision | tuple[GateResult, ...]]:
        yield self.decision
        yield self.results


class GateEngine:
    """Determines final status only from explicit evidence."""

    REQUIRED_GATES = (
        "G0_ENVIRONMENT",
        "G1_WORKSPACE",
        "G2_TASK",
        "G3_EXECUTION",
        "G4_TEST",
        "G5_GIT",
        "G6_EVIDENCE",
    )

    def __init__(self, evidence_verifier: EvidenceVerifier | None = None) -> None:
        self.evidence_verifier = evidence_verifier or EvidenceVerifier()
        self._approval_authority = object()

    def evaluate_gates(
        self,
        checks: dict[str, bool | None | GateStatus],
        *,
        evidence: VerifiedRunEvidence | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        agent_id: str | None = None,
    ) -> GateEvaluation:
        results: list[GateResult] = []
        unexpected_gates = set(checks) - set(self.REQUIRED_GATES)
        for gate_id in self.REQUIRED_GATES:
            verified = checks.get(gate_id)
            if gate_id == "G6_EVIDENCE":
                verified_proof = self.evidence_verifier.verifies(
                    evidence,
                    task_id=task_id,
                    run_id=run_id,
                    agent_id=agent_id,
                )
                if not verified_proof:
                    verified = None
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

        for gate_id in sorted(unexpected_gates):
            results.append(
                GateResult(
                    gate_id,
                    GateStatus.BLOCKED,
                    "unregistered Gate cannot be evaluated",
                )
            )

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
        approval = None
        if decision is GateDecision.PASS and evidence is not None:
            approval = _GateApproval(
                task_id=task_id or "",
                run_id=run_id or "",
                agent_id=agent_id or "",
                evidence_digest=evidence.evidence_digest,
                authority=self._approval_authority,
            )
        return GateEvaluation(decision, tuple(results), evidence, approval)

    def transition_task_to_pass(
        self,
        task: TaskRecord,
        evaluation: GateEvaluation,
        *,
        run_record: RunRecord,
        evidence_record: EvidenceRecord,
    ) -> None:
        evidence_record.approval_signature = self.issue_database_approval(
            task,
            evaluation,
            run_record=run_record,
            evidence_record=evidence_record,
        )
        with _gate_pass_scope(task.id, _GATE_PASS_AUTHORITY):
            task.status = "pass"

    def issue_database_approval(
        self,
        task: TaskRecord,
        evaluation: GateEvaluation,
        *,
        run_record: RunRecord,
        evidence_record: EvidenceRecord,
    ) -> str:
        approval = evaluation.approval
        evidence = evaluation.evidence
        if (
            evaluation.decision is not GateDecision.PASS
            or approval is None
            or approval.authority is not self._approval_authority
            or approval.task_id != task.id
            or task.status != "verifying"
            or evidence is None
            or not self.evidence_verifier.verifies(
                evidence,
                task_id=approval.task_id,
                run_id=approval.run_id,
                agent_id=approval.agent_id,
            )
            or evidence.evidence_digest != approval.evidence_digest
            or tuple(result.id for result in evaluation.results)
            != self.REQUIRED_GATES
            or any(result.status is not GateStatus.PASS for result in evaluation.results)
            or run_record.id != approval.run_id
            or run_record.task_id != task.id
            or run_record.agent_id != approval.agent_id
            or run_record.status != "completed"
            or run_record.initial_commit != evidence.initial_commit
            or run_record.final_commit != evidence.final_commit
            or evidence_record.task_id != task.id
            or evidence_record.run_id != approval.run_id
            or evidence_record.type != "execution"
            or evidence_record.producer != approval.agent_id
            or evidence_record.result != GateDecision.PASS.value
            or evidence_record.verification_digest != evidence.evidence_digest
            or evidence_record.metadata_json != run_record.evidence_json
        ):
            raise PermissionError("Task PASS requires a verified Gate approval")
        try:
            persisted = json.loads(evidence_record.metadata_json)
        except json.JSONDecodeError as error:
            raise PermissionError("Gate approval evidence is not valid JSON") from error
        gate_results = persisted.get("gate_results") if isinstance(persisted, dict) else None
        if (
            not isinstance(persisted, dict)
            or persisted.get("task_id") != task.id
            or persisted.get("run_id") != approval.run_id
            or persisted.get("agent_id") != approval.agent_id
            or persisted.get("execution_result") != GateDecision.PASS.value
            or persisted.get("gate_decision") != GateDecision.PASS.value
            or persisted.get("verification_digest") != evidence.evidence_digest
            or persisted.get("initial_commit") != evidence.initial_commit
            or persisted.get("final_commit") != evidence.final_commit
            or persisted.get("workspace") != run_record.workspace
            or persisted.get("commit_created") is not True
            or persisted.get("test_exit_code") != 0
            or not isinstance(gate_results, list)
            or tuple(
                item.get("id") for item in gate_results if isinstance(item, dict)
            )
            != self.REQUIRED_GATES
            or any(
                not isinstance(item, dict)
                or item.get("status") != GateStatus.PASS.value
                for item in gate_results
            )
        ):
            raise PermissionError("persisted evidence does not match Gate approval")
        return _issue_gate_approval(
            task_id=task.id,
            run_id=run_record.id,
            agent_id=run_record.agent_id,
            run_status=run_record.status,
            initial_commit=run_record.initial_commit,
            final_commit=run_record.final_commit,
            workspace=run_record.workspace,
            evidence_type=evidence_record.type,
            evidence_producer=evidence_record.producer,
            evidence_result=evidence_record.result,
            verification_digest=evidence_record.verification_digest,
            metadata_json=evidence_record.metadata_json,
        )
