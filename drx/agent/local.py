import json
import subprocess
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from drx.agent.capabilities import AgentCapabilities, AgentCapability
from drx.agent.config import TestConfig
from drx.agent.run import AgentRun, AgentRunResult, AgentRunState
from drx.core.gate import GateDecision, GateEngine, GateStatus
from drx.core.task import Task, TaskStatus, transition_task
from drx.evidence.execution import AgentEvidence, redact_secrets
from drx.evidence.git import GitEvidence, GitEvidenceCollector
from drx.evidence.test import TestEvidence
from drx.evidence.verifier import EvidenceVerificationError, EvidenceVerifier
from drx.execution.lifecycle import ExecutionLifecycle
from drx.execution.prompt import TaskPromptBuilder, TaskPromptInput
from drx.execution.test_runner import TestRunner
from drx.git.service import GitService
from drx.models.agent import AgentRecord
from drx.models.evidence import EvidenceRecord
from drx.models.gate import GateRecord
from drx.models.project import ProjectRecord
from drx.models.requirement import RequirementRecord
from drx.models.run import RunRecord
from drx.models.task import TaskRecord
from drx.policy.engine import PolicyEngine
from drx.runtime.interface import ModelRuntime
from drx.workspace.manager import Workspace, WorkspaceError, WorkspaceManager


class AgentRunFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        gate_id: str,
        state: AgentRunState = AgentRunState.BLOCKED,
    ) -> None:
        super().__init__(message)
        self.gate_id = gate_id
        self.state = state


class LocalCodingAgent:
    def __init__(
        self,
        *,
        agent_id: str,
        name: str,
        runtime: ModelRuntime,
        workspace_manager: WorkspaceManager,
        test_config: TestConfig | None = None,
        policy_engine: PolicyEngine | None = None,
        allow_local_commit: bool = True,
    ) -> None:
        self.agent_id = agent_id
        self.name = name
        self.runtime = runtime
        self.workspace_manager = workspace_manager
        self.test_runner = TestRunner(test_config)
        self.prompt_builder = TaskPromptBuilder()
        self.evidence_verifier = EvidenceVerifier()
        self.gate_engine = GateEngine(self.evidence_verifier)
        self.policy_engine = policy_engine or PolicyEngine()
        self.allow_local_commit = allow_local_commit

    def identify(self) -> dict[str, str]:
        return {"id": self.agent_id, "name": self.name, "type": "local-coding-agent"}

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            (
                AgentCapability("task-prompting", "Builds deterministic task prompts"),
                AgentCapability("runtime-health-check", "Checks model runtime availability"),
                AgentCapability("runtime-generation", "Requests model-generated changes"),
                AgentCapability("workspace-isolation", "Uses a managed Git workspace"),
                AgentCapability("workspace-context-read", "Reads permitted workspace context"),
                AgentCapability("unified-diff-only", "Applies validated unified diffs"),
                AgentCapability("test-execution", "Executes the configured Python test suite"),
                AgentCapability("git-evidence", "Collects status, diff, and commit evidence"),
                AgentCapability("git-commit", "Creates a managed local Git commit"),
            )
        )

    def health_check(self) -> bool:
        return self.runtime.health_check()

    def prepare_workspace(
        self, workspace_id: str, repository: str, branch: str | None = None
    ) -> Workspace:
        try:
            return self.workspace_manager.create(
                workspace_id, repository, branch=branch
            )
        except (WorkspaceError, OSError, subprocess.TimeoutExpired) as error:
            raise AgentRunFailure(
                "workspace is unavailable or could not be prepared",
                gate_id="G1_WORKSPACE",
            ) from error

    def build_task_prompt(
        self,
        *,
        project_id: str,
        project_name: str,
        task_id: str,
        objective: str,
        acceptance_criteria: tuple[str, ...],
        workspace_path: str,
        workspace_context: str,
    ) -> str:
        return self.prompt_builder.build(
            TaskPromptInput(
                project_id=project_id,
                project_name=project_name,
                task_id=task_id,
                objective=objective,
                acceptance_criteria=acceptance_criteria,
                workspace_path=workspace_path,
                required_tests=("Run the detected Python test suite.",),
                workspace_context=workspace_context,
            )
        )

    def start_run(
        self,
        session: Session,
        *,
        run_id: str,
        task_id: str,
        runtime_id: str,
    ) -> tuple[AgentRun, RunRecord]:
        agent_run = AgentRun(
            id=run_id,
            task_id=task_id,
            agent_id=self.agent_id,
            runtime_id=runtime_id,
        )
        record = RunRecord(
            id=run_id,
            task_id=task_id,
            agent_id=self.agent_id,
            runtime_id=runtime_id,
            status=agent_run.state.value,
            started_at=agent_run.started_at,
        )
        session.add(record)
        if session.get(AgentRecord, self.agent_id) is None:
            session.add(
                AgentRecord(
                    id=self.agent_id,
                    name=self.name,
                    adapter_type="local-coding-agent",
                )
            )
        session.commit()
        return agent_run, record

    def execute(
        self,
        session: Session,
        task_id: str,
        *,
        branch: str | None = None,
    ) -> AgentRunResult:
        task_record = session.get(TaskRecord, task_id)
        if task_record is None:
            raise ValueError(f"unknown task: {task_id}")
        requirement = session.get(RequirementRecord, task_record.requirement_id)
        project = (
            session.get(ProjectRecord, requirement.project_id)
            if requirement is not None
            else None
        )
        if requirement is None or project is None:
            raise ValueError("task is missing its requirement or project")

        run_id = uuid.uuid4().hex
        runtime_identity = self.runtime.identify()
        runtime_id = f"{runtime_identity['type']}:{runtime_identity['model']}"
        agent_run, run_record = self.start_run(
            session, run_id=run_id, task_id=task_id, runtime_id=runtime_id
        )
        checks: dict[str, bool | None | GateStatus] = {
            "G0_ENVIRONMENT": None,
            "G1_WORKSPACE": None,
            "G2_TASK": None,
            "G3_EXECUTION": None,
            "G4_TEST": None,
            "G5_GIT": None,
            "G6_EVIDENCE": None,
        }
        workspace: Workspace | None = None
        git_evidence: GitEvidence | None = None
        test_result: TestEvidence | None = None
        failure_reason = ""

        try:
            self._transition_run(session, agent_run, run_record, AgentRunState.PREPARING)
            self._authorize("task.execute", "G2_TASK", checks)
            self._authorize("runtime.health_check", "G0_ENVIRONMENT", checks)
            try:
                runtime_available = self.runtime.health_check()
            except (httpx.HTTPError, RuntimeError) as error:
                raise AgentRunFailure(
                    "configured model runtime is unavailable",
                    gate_id="G0_ENVIRONMENT",
                ) from error
            if not runtime_available:
                raise AgentRunFailure(
                    "configured model runtime is unavailable",
                    gate_id="G0_ENVIRONMENT",
                )
            checks["G0_ENVIRONMENT"] = True

            if not task_record.objective.strip():
                raise AgentRunFailure("task objective is empty", gate_id="G2_TASK")
            try:
                raw_criteria = json.loads(task_record.acceptance_criteria_json)
            except (TypeError, json.JSONDecodeError) as error:
                raise AgentRunFailure(
                    "task acceptance criteria are invalid", gate_id="G2_TASK"
                ) from error
            if (
                not isinstance(raw_criteria, list)
                or not raw_criteria
                or not all(
                    isinstance(item, str) and item.strip() for item in raw_criteria
                )
            ):
                raise AgentRunFailure(
                    "task must include acceptance criteria", gate_id="G2_TASK"
                )
            criteria = tuple(
                item for item in raw_criteria if isinstance(item, str)
            )
            try:
                task = Task(
                    task_record.id,
                    task_record.requirement_id,
                    task_record.title,
                    TaskStatus(task_record.status),
                )
                task = transition_task(task, TaskStatus.IN_PROGRESS)
            except ValueError as error:
                raise AgentRunFailure(
                    "task is not in an executable state", gate_id="G2_TASK"
                ) from error
            task_record.status = task.status.value
            checks["G2_TASK"] = True
            session.commit()

            self._transition_run(session, agent_run, run_record, AgentRunState.RUNNING)
            self._authorize("workspace.create", "G1_WORKSPACE", checks)
            workspace = self.prepare_workspace(
                f"run-{run_id}", project.repository, branch
            )
            run_record.workspace = str(workspace.repository)
            run_record.initial_commit = workspace.initial_head
            checks["G1_WORKSPACE"] = True
            session.commit()

            try:
                self._authorize("workspace.context.read", "G1_WORKSPACE", checks)
                context = self.workspace_manager.collect_context(workspace)
            except (WorkspaceError, OSError, subprocess.SubprocessError) as error:
                raise AgentRunFailure(
                    "workspace context could not be collected",
                    gate_id="G1_WORKSPACE",
                ) from error
            prompt = self.build_task_prompt(
                project_id=project.id,
                project_name=project.name,
                task_id=task_record.id,
                objective=task_record.objective,
                acceptance_criteria=criteria,
                workspace_path=str(workspace.repository),
                workspace_context=context,
            )
            self._authorize("runtime.generate", "G3_EXECUTION", checks)
            try:
                patch = self.runtime.generate(prompt)
            except (httpx.HTTPError, RuntimeError, ValueError) as error:
                raise AgentRunFailure(
                    "model runtime failed during generation",
                    gate_id="G3_EXECUTION",
                    state=AgentRunState.FAILED,
                ) from error
            collector = GitEvidenceCollector(workspace.repository)
            try:
                self._authorize("workspace.apply_diff", "G3_EXECUTION", checks)
                collector.apply_patch(patch)
                self._authorize("git.collect_evidence", "G5_GIT", checks)
                git_evidence = collector.collect(workspace.initial_head)
            except PermissionError as error:
                raise AgentRunFailure(
                    "runtime diff violates workspace security restrictions",
                    gate_id="G3_EXECUTION",
                ) from error
            except (RuntimeError, ValueError, subprocess.SubprocessError) as error:
                raise AgentRunFailure(
                    f"runtime did not produce an applicable unified diff: {error}",
                    gate_id="G3_EXECUTION",
                ) from error

            checks["G3_EXECUTION"] = bool(git_evidence.diff.strip())
            if not checks["G3_EXECUTION"]:
                raise AgentRunFailure(
                    "model produced no verifiable workspace changes",
                    gate_id="G3_EXECUTION",
                    state=AgentRunState.FAILED,
                )
            task = transition_task(task, TaskStatus.IMPLEMENTED)
            task_record.status = task.status.value
            session.flush()
            task = transition_task(task, TaskStatus.VERIFYING)
            task_record.status = task.status.value
            self._transition_run(session, agent_run, run_record, AgentRunState.CHANGED)
            self._transition_run(session, agent_run, run_record, AgentRunState.TESTING)
            session.commit()

            try:
                self._authorize("tests.run", "G4_TEST", checks)
                test_result = self.test_runner.run(workspace.repository)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                raise AgentRunFailure(
                    "no configured Python test suite could be found",
                    gate_id="G4_TEST",
                ) from error
            checks["G4_TEST"] = test_result.passed
            test_state = (
                AgentRunState.TEST_PASS
                if test_result.passed
                else AgentRunState.TEST_FAIL
            )
            self._transition_run(session, agent_run, run_record, test_state)
            if test_result.passed:
                if git_evidence is None or workspace is None:
                    raise AgentRunFailure(
                        "pre-test Git evidence is unavailable",
                        gate_id="G5_GIT",
                        state=AgentRunState.FAILED,
                    )
                try:
                    self._authorize("git.collect_evidence", "G5_GIT", checks)
                    post_test_git = collector.collect(workspace.initial_head)
                except (RuntimeError, subprocess.SubprocessError) as error:
                    raise AgentRunFailure(
                        "Git evidence could not be collected after tests",
                        gate_id="G5_GIT",
                        state=AgentRunState.FAILED,
                    ) from error
                if (
                    post_test_git.final_head != git_evidence.final_head
                    or post_test_git.branch != git_evidence.branch
                    or post_test_git.changed_files != git_evidence.changed_files
                    or post_test_git.diff != git_evidence.diff
                ):
                    git_evidence = post_test_git
                    checks["G4_TEST"] = None
                    raise AgentRunFailure(
                        "workspace changed during test execution; test results cannot "
                        "be bound to the committed diff",
                        gate_id="G4_TEST",
                        state=AgentRunState.FAILED,
                    )
            if not test_result.passed:
                task = transition_task(task, TaskStatus.FAIL)
                task_record.status = task.status.value
            session.commit()

            commit_failed = False
            if test_result.passed and self.allow_local_commit:
                self._authorize("git.commit", "G5_GIT", checks)
                try:
                    GitService(
                        workspace.repository, allow_local_commit=True
                    ).commit(f"DevRelay-X task {task_record.id}")
                except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                    commit_failed = True
                    failure_reason = f"local workspace commit failed: {error}"

            try:
                self._authorize("git.collect_evidence", "G5_GIT", checks)
                git_evidence = collector.collect(workspace.initial_head)
            except (RuntimeError, subprocess.SubprocessError) as error:
                raise AgentRunFailure(
                    "Git evidence could not be collected", gate_id="G5_GIT"
                ) from error
            checks["G5_GIT"] = (
                not commit_failed
                and bool(git_evidence.changed_files)
                and git_evidence.commit_created
            )
            self._transition_run(
                session, agent_run, run_record, AgentRunState.EVIDENCE_COLLECTED
            )
            session.commit()
        except AgentRunFailure as error:
            failure_reason = str(error)
            if error.gate_id in checks and checks[error.gate_id] is None:
                checks[error.gate_id] = None
            if agent_run.state not in {
                AgentRunState.FAILED,
                AgentRunState.BLOCKED,
                AgentRunState.CANCELLED,
            }:
                self._transition_run(session, agent_run, run_record, error.state)
            if task_record.status in {
                TaskStatus.CREATED.value,
                TaskStatus.ASSIGNED.value,
                TaskStatus.IN_PROGRESS.value,
                TaskStatus.IMPLEMENTED.value,
                TaskStatus.VERIFYING.value,
            }:
                blocked_task = transition_task(
                    Task(task_record.id, task_record.requirement_id, task_record.title,
                         TaskStatus(task_record.status)),
                    TaskStatus.BLOCKED,
                )
                task_record.status = blocked_task.status.value
            session.commit()

        ended_at = datetime.now(UTC)
        provisional_evidence = AgentEvidence(
            task_id=task_id,
            run_id=run_id,
            agent_id=self.agent_id,
            runtime_id=runtime_id,
            workspace=str(workspace.repository) if workspace else "",
            started_at=agent_run.started_at,
            ended_at=ended_at,
            initial_commit=workspace.initial_head if workspace else "",
            final_commit=git_evidence.final_head if git_evidence else "",
            branch=git_evidence.branch if git_evidence else "",
            commit_created=git_evidence.commit_created if git_evidence else False,
            changed_files=git_evidence.changed_files if git_evidence else (),
            test_command=test_result.command if test_result else (),
            test_exit_code=test_result.exit_code if test_result else None,
            test_stdout=test_result.stdout if test_result else "",
            test_stderr=test_result.stderr if test_result else "",
            test_output=(
                (test_result.stdout + "\n" + test_result.stderr)
                if test_result
                else ""
            ),
            test_duration_seconds=test_result.duration_seconds if test_result else None,
            git_status=git_evidence.status if git_evidence else "",
            diff_summary=git_evidence.diff_summary if git_evidence else "",
            diff=git_evidence.diff if git_evidence else "",
            execution_result="pending",
            failure_reason=failure_reason,
            state_transitions=tuple(agent_run.transitions),
        )
        evidence_row = EvidenceRecord(
            id=uuid.uuid4().hex,
            task_id=task_id,
            run_id=run_id,
            type="execution",
            producer=self.agent_id,
            result="pending",
            metadata_json=json.dumps(provisional_evidence.to_dict(), ensure_ascii=True),
        )
        session.add(evidence_row)
        run_record.final_commit = provisional_evidence.final_commit
        session.flush()
        verified_evidence = None
        if (
            task_record.status == TaskStatus.VERIFYING
            and agent_run.state is AgentRunState.EVIDENCE_COLLECTED
            and test_result is not None
            and git_evidence is not None
        ):
            try:
                verified_evidence = self.evidence_verifier.attest(
                    task=task_record,
                    run=agent_run,
                    run_record=run_record,
                    evidence_record=evidence_row,
                    evidence=provisional_evidence,
                    git_evidence=git_evidence,
                    test_evidence=test_result,
                )
            except EvidenceVerificationError as error:
                verification_failure = (
                    f"execution evidence could not be verified: {error}"
                )
                failure_reason = (
                    f"{failure_reason}; {verification_failure}"
                    if failure_reason
                    else verification_failure
                )
        checks["G6_EVIDENCE"] = verified_evidence is not None

        gate_evaluation = self.gate_engine.evaluate_gates(
            checks,
            evidence=verified_evidence,
            task_id=task_id,
            run_id=run_id,
            agent_id=self.agent_id,
        )
        gate_decision = gate_evaluation.decision
        gate_results = gate_evaluation.results
        if agent_run.state in {
            AgentRunState.TEST_PASS,
            AgentRunState.TEST_FAIL,
        }:
            self._transition_run(
                session, agent_run, run_record, AgentRunState.EVIDENCE_COLLECTED
            )
        if agent_run.state == AgentRunState.EVIDENCE_COLLECTED:
            self._transition_run(
                session, agent_run, run_record, AgentRunState.GATE_PENDING
            )
        if agent_run.state == AgentRunState.GATE_PENDING:
            final_state = (
                AgentRunState.COMPLETED
                if gate_decision == GateDecision.PASS
                else AgentRunState.FAILED
                if gate_decision == GateDecision.FAIL
                else AgentRunState.BLOCKED
            )
            self._transition_run(session, agent_run, run_record, final_state)

        if gate_decision == GateDecision.FAIL and task_record.status == TaskStatus.VERIFYING:
            self._transition_task_record(task_record, TaskStatus.FAIL)
        elif gate_decision in {GateDecision.BLOCKED, GateDecision.HOLD} and task_record.status in {
            TaskStatus.CREATED.value,
            TaskStatus.ASSIGNED.value,
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.IMPLEMENTED.value,
            TaskStatus.VERIFYING.value,
        }:
            self._transition_task_record(task_record, TaskStatus.BLOCKED)

        evidence = replace(
            provisional_evidence,
            execution_result=gate_decision.value,
            gate_decision=gate_decision.value,
            gate_results=tuple(
                {"id": result.id, "status": result.status.value, "reason": result.reason}
                for result in gate_results
            ),
            state_transitions=tuple(agent_run.transitions),
        )
        evidence_data = evidence.to_dict()
        evidence_json = json.dumps(evidence_data, ensure_ascii=True)
        run_record.ended_at = ended_at
        run_record.final_commit = evidence.final_commit
        run_record.evidence_json = evidence_json
        run_record.failure_reason = redact_secrets(failure_reason)
        evidence_row.result = gate_decision.value
        evidence_row.metadata_json = evidence_json
        for result in gate_results:
            session.add(
                GateRecord(
                    id=f"{run_id}:{result.id}",
                    task_id=task_id,
                    run_id=run_id,
                    decision=result.status.value,
                    reason=result.reason,
                )
            )
        session.flush()
        if gate_decision == GateDecision.PASS:
            self.gate_engine.transition_task_to_pass(task_record, gate_evaluation)
        session.commit()
        return AgentRunResult(run_id, agent_run.state, gate_decision.value, evidence_data)

    def collect_changes(self, workspace: Workspace) -> GitEvidence:
        return GitEvidenceCollector(workspace.repository).collect(workspace.initial_head)

    def run_tests(self, workspace: Workspace) -> TestEvidence:
        return self.test_runner.run(workspace.repository)

    def collect_evidence(
        self, session: Session, result: AgentRunResult
    ) -> dict[str, Any]:
        record = session.execute(
            select(EvidenceRecord).where(EvidenceRecord.run_id == result.run_id)
        ).scalar_one()
        payload = json.loads(record.metadata_json)
        if not isinstance(payload, dict):
            raise ValueError("persisted AgentRun evidence is not a JSON object")
        run_record = session.get(RunRecord, result.run_id)
        task_record = session.get(TaskRecord, record.task_id)
        if (
            run_record is None
            or task_record is None
            or record.type != "execution"
            or record.run_id != result.run_id
            or record.producer != run_record.agent_id
            or run_record.task_id != record.task_id
            or run_record.status != result.state.value
            or record.result != result.gate_decision
            or payload != result.evidence
            or run_record.evidence_json != record.metadata_json
            or payload.get("gate_decision") != result.gate_decision
        ):
            raise ValueError("AgentRun and persisted execution evidence do not match")
        if result.gate_decision == GateDecision.PASS.value:
            gate_records = session.scalars(
                select(GateRecord).where(GateRecord.run_id == result.run_id)
            ).all()
            if (
                task_record.status != TaskStatus.PASS.value
                or run_record.status != AgentRunState.COMPLETED.value
                or tuple(sorted(gate_record.id.split(":", 1)[-1] for gate_record in gate_records))
                != tuple(sorted(GateEngine.REQUIRED_GATES))
                or any(
                    gate_record.decision != GateStatus.PASS.value
                    for gate_record in gate_records
                )
                or payload.get("test_exit_code") != 0
                or payload.get("commit_created") is not True
                or payload.get("final_commit") != run_record.final_commit
            ):
                raise ValueError("PASS evidence is missing a verified Gate or Git result")
        return payload

    def finalize_run(self, session: Session, run_id: str) -> RunRecord:
        run_record = session.get(RunRecord, run_id)
        if run_record is None or run_record.ended_at is None:
            raise ValueError("AgentRun has not been finalized")
        return run_record

    def stop(self) -> None:
        self.runtime.shutdown()

    @staticmethod
    def _transition_run(
        session: Session,
        agent_run: AgentRun,
        record: RunRecord,
        next_state: AgentRunState,
    ) -> None:
        ExecutionLifecycle.transition_run(session, agent_run, record, next_state)

    @staticmethod
    def _transition_task_record(
        task_record: TaskRecord, next_status: TaskStatus
    ) -> None:
        ExecutionLifecycle.transition_task(task_record, next_status)

    def _authorize(
        self,
        action: str,
        gate_id: str,
        checks: dict[str, bool | None | GateStatus],
    ) -> None:
        passed_gates = {
            check_id for check_id, passed in checks.items() if passed is True
        }
        result = self.policy_engine.evaluate(
            action,
            capabilities=self.capabilities(),
            passed_gates=passed_gates,
        )
        if not result.allowed:
            raise AgentRunFailure(
                f"policy denied action '{action}': {result.reason}",
                gate_id=gate_id,
            )
