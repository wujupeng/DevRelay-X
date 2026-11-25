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
from drx.execution.lifecycle import ExecutionLifecycle
from drx.execution.prompt import TaskPromptBuilder, TaskPromptInput
from drx.execution.test_runner import TestRunner
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
    ) -> None:
        self.agent_id = agent_id
        self.name = name
        self.runtime = runtime
        self.workspace_manager = workspace_manager
        self.test_runner = TestRunner(test_config)
        self.prompt_builder = TaskPromptBuilder()
        self.gate_engine = GateEngine()
        self.policy_engine = policy_engine or PolicyEngine()

    def identify(self) -> dict[str, str]:
        return {"id": self.agent_id, "name": self.name, "type": "local-coding-agent"}

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            (
                AgentCapability("task-prompting", "Builds deterministic task prompts"),
                AgentCapability("workspace-isolation", "Uses a managed Git workspace"),
                AgentCapability("unified-diff-only", "Applies validated unified diffs"),
                AgentCapability("test-execution", "Executes the configured Python test suite"),
                AgentCapability("git-evidence", "Collects status, diff, and commit evidence"),
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
            self._authorize("task.execute", "G2_TASK")
            self._authorize("runtime.health_check", "G0_ENVIRONMENT")
            try:
                runtime_available = self.runtime.health_check()
            except httpx.HTTPError as error:
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
                transition_task(task, TaskStatus.IN_PROGRESS)
            except ValueError as error:
                raise AgentRunFailure(
                    "task is not in an executable state", gate_id="G2_TASK"
                ) from error
            task_record.status = task.status.value
            checks["G2_TASK"] = True
            session.commit()

            self._transition_run(session, agent_run, run_record, AgentRunState.RUNNING)
            self._authorize("workspace.create", "G1_WORKSPACE")
            workspace = self.prepare_workspace(
                f"run-{run_id}", project.repository, branch
            )
            run_record.workspace = str(workspace.repository)
            run_record.initial_commit = workspace.initial_head
            checks["G1_WORKSPACE"] = True
            session.commit()

            try:
                self._authorize("workspace.context.read", "G1_WORKSPACE")
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
            self._authorize("runtime.generate", "G3_EXECUTION")
            try:
                patch = self.runtime.generate(prompt)
            except (httpx.HTTPError, ValueError) as error:
                raise AgentRunFailure(
                    "model runtime failed during generation",
                    gate_id="G3_EXECUTION",
                    state=AgentRunState.FAILED,
                ) from error
            collector = GitEvidenceCollector(workspace.repository)
            try:
                self._authorize("workspace.apply_diff", "G3_EXECUTION")
                collector.apply_patch(patch)
                self._authorize("git.collect_evidence", "G5_GIT")
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
            transition_task(task, TaskStatus.IMPLEMENTED)
            transition_task(task, TaskStatus.VERIFYING)
            task_record.status = task.status.value
            self._transition_run(session, agent_run, run_record, AgentRunState.CHANGED)
            self._transition_run(session, agent_run, run_record, AgentRunState.TESTING)
            session.commit()

            try:
                self._authorize("tests.run", "G4_TEST")
                test_result = self.test_runner.run(workspace.repository)
            except (OSError, subprocess.SubprocessError) as error:
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
            if not test_result.passed:
                transition_task(task, TaskStatus.FAIL)
                task_record.status = task.status.value
            session.commit()

            try:
                self._authorize("git.collect_evidence", "G5_GIT")
                git_evidence = collector.collect(workspace.initial_head)
            except (RuntimeError, subprocess.SubprocessError) as error:
                raise AgentRunFailure(
                    "Git evidence could not be collected", gate_id="G5_GIT"
                ) from error
            checks["G5_GIT"] = True
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
                transition_task(
                    Task(task_record.id, task_record.requirement_id, task_record.title,
                         TaskStatus(task_record.status)),
                    TaskStatus.BLOCKED,
                )
                task_record.status = TaskStatus.BLOCKED.value
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
        session.flush()
        checks["G6_EVIDENCE"] = True

        gate_decision, gate_results = self.gate_engine.evaluate_gates(checks)
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

        if gate_decision == GateDecision.PASS and task_record.status == TaskStatus.VERIFYING:
            self._transition_task_record(task_record, TaskStatus.PASS)
        elif gate_decision == GateDecision.FAIL and task_record.status == TaskStatus.VERIFYING:
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

    def _authorize(self, action: str, gate_id: str) -> None:
        result = self.policy_engine.evaluate(action)
        if not result.allowed:
            raise AgentRunFailure(
                f"policy denied action '{action}': {result.reason}",
                gate_id=gate_id,
            )
