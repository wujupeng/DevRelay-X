import difflib
import json
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from drx.agent.config import TestConfig
from drx.agent.local import LocalCodingAgent
from drx.agent.run import AgentRunState
from drx.models.evidence import EvidenceRecord
from drx.models.project import ProjectRecord
from drx.models.requirement import RequirementRecord
from drx.models.run import RunRecord
from drx.models.task import TaskRecord
from drx.policy.engine import PolicyEngine
from drx.storage.database import create_database
from drx.workspace.manager import WorkspaceManager


class FixtureRuntime:
    __test__ = False

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.closed = False
        self.generate_called = False

    def identify(self) -> dict[str, str]:
        return {"type": "test-runtime", "model": "deterministic-fixture"}

    def capabilities(self) -> list[str]:
        return ["text-generation"]

    def health_check(self) -> bool:
        return self.available

    def generate(self, prompt: str) -> str:
        self.generate_called = True
        assert "Do not declare or imply Gate PASS" in prompt
        workspace_path = prompt.split("Allowed workspace: ", maxsplit=1)[1].splitlines()[0]
        source_path = Path(workspace_path) / "calculator.py"
        old_source = source_path.read_text(encoding="utf-8")
        new_source = old_source.replace("return None", "return left + right", 1)
        new_source = new_source.replace("return None", "return left - right", 1)
        diff = difflib.unified_diff(
            old_source.splitlines(keepends=True),
            new_source.splitlines(keepends=True),
            fromfile="a/calculator.py",
            tofile="b/calculator.py",
        )
        return "diff --git a/calculator.py b/calculator.py\n" + "".join(diff)

    def stream(self, prompt: str) -> Iterator[str]:
        yield self.generate(prompt)

    def estimate_capability(self, task: str) -> dict[str, object]:
        return {"fixture": True, "task_length": len(task)}

    def shutdown(self) -> None:
        self.closed = True


class FailingFixtureRuntime(FixtureRuntime):
    def generate(self, prompt: str) -> str:
        return super().generate(prompt).replace(
            "+    return left + right", "+    return 0"
        )


def test_policy_denial_prevents_runtime_generation(
    tmp_path: Path, database_session: Session
) -> None:
    repository = tmp_path / "calculator"
    shutil.copytree(Path(__file__).parents[1] / "fixtures" / "calculator", repository)
    _make_git_repository(repository)
    _create_task(database_session, repository)
    runtime = FixtureRuntime()
    policy = PolicyEngine(
        allowed_actions=PolicyEngine.DEFAULT_ALLOWED_ACTIONS - {"runtime.generate"}
    )
    agent = LocalCodingAgent(
        agent_id="local-agent-001",
        name="Local Coding Agent",
        runtime=runtime,
        workspace_manager=WorkspaceManager(
            tmp_path / "workspaces", control_repository=tmp_path / "control"
        ),
        policy_engine=policy,
    )

    result = agent.execute(database_session, "calculator-task")

    assert result.state is AgentRunState.BLOCKED
    assert result.gate_decision == "blocked"
    assert "policy denied action 'runtime.generate'" in result.evidence["failure_reason"]
    assert not runtime.generate_called
    assert database_session.get(TaskRecord, "calculator-task").status == "blocked"


@pytest.fixture
def database_session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_database(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _create_task(session: Session, repository: Path) -> None:
    session.add(
        ProjectRecord(
            id="calculator-project",
            name="Calculator",
            repository=str(repository),
            description="E2E fixture project",
        )
    )
    session.flush()
    session.add(
        RequirementRecord(
            id="calculator-requirement",
            project_id="calculator-project",
            title="Calculator functions",
            description="Implement arithmetic",
            status="approved",
        )
    )
    session.flush()
    session.add(
        TaskRecord(
            id="calculator-task",
            requirement_id="calculator-requirement",
            title="Implement calculator functions",
            objective="Implement missing add and subtract functions.",
            acceptance_criteria_json=json.dumps(
                ["add returns the sum", "subtract returns the difference"]
            ),
        )
    )
    session.commit()


def _make_git_repository(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "DevRelay E2E"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "devrelay-e2e@example.invalid"],
        cwd=path,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "calculator fixture"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def test_task_to_agent_run_evidence_and_gate(tmp_path: Path, database_session: Session) -> None:
    repository = tmp_path / "calculator"
    fixture_source = Path(__file__).parents[1] / "fixtures" / "calculator"
    shutil.copytree(fixture_source, repository)
    _make_git_repository(repository)
    _create_task(database_session, repository)

    agent = LocalCodingAgent(
        agent_id="local-agent-001",
        name="Local Coding Agent",
        runtime=FixtureRuntime(),
        workspace_manager=WorkspaceManager(
            tmp_path / "workspaces", control_repository=tmp_path / "control"
        ),
        test_config=TestConfig(framework="pytest"),
    )
    result = agent.execute(database_session, "calculator-task")

    assert result.state is AgentRunState.COMPLETED, result.evidence["failure_reason"]
    assert result.gate_decision == "pass"
    assert result.evidence["state_transitions"] == [
        "created",
        "preparing",
        "running",
        "changed",
        "testing",
        "test_pass",
        "evidence_collected",
        "gate_pending",
        "completed",
    ]
    assert result.evidence["changed_files"] == ["calculator.py"]
    assert result.evidence["test_exit_code"] == 0
    assert "2 passed" in result.evidence["test_output"]
    assert "+    return left + right" in result.evidence["diff"]
    assert all(gate["status"] == "pass" for gate in result.evidence["gate_results"])

    task = database_session.get(TaskRecord, "calculator-task")
    run = database_session.get(RunRecord, result.run_id)
    evidence = database_session.scalars(
        select(EvidenceRecord).where(EvidenceRecord.run_id == result.run_id)
    ).one()
    assert task is not None and task.status == "pass"
    assert run is not None and run.status == "completed" and run.final_commit
    assert evidence.result == "pass"


def test_runtime_unavailable_is_persisted_as_blocked(
    tmp_path: Path, database_session: Session
) -> None:
    repository = tmp_path / "calculator"
    shutil.copytree(Path(__file__).parents[1] / "fixtures" / "calculator", repository)
    _make_git_repository(repository)
    _create_task(database_session, repository)
    agent = LocalCodingAgent(
        agent_id="local-agent-001",
        name="Local Coding Agent",
        runtime=FixtureRuntime(available=False),
        workspace_manager=WorkspaceManager(
            tmp_path / "workspaces", control_repository=tmp_path / "control"
        ),
    )

    result = agent.execute(database_session, "calculator-task")

    assert result.state is AgentRunState.BLOCKED
    assert result.gate_decision == "blocked"
    assert result.evidence["failure_reason"] == "configured model runtime is unavailable"
    assert result.evidence["gate_results"][0]["id"] == "G0_ENVIRONMENT"
    assert database_session.get(RunRecord, result.run_id).status == "blocked"


def test_real_test_failure_fails_gate_and_task(
    tmp_path: Path, database_session: Session
) -> None:
    repository = tmp_path / "calculator"
    shutil.copytree(Path(__file__).parents[1] / "fixtures" / "calculator", repository)
    _make_git_repository(repository)
    _create_task(database_session, repository)
    agent = LocalCodingAgent(
        agent_id="local-agent-001",
        name="Local Coding Agent",
        runtime=FailingFixtureRuntime(),
        workspace_manager=WorkspaceManager(
            tmp_path / "workspaces", control_repository=tmp_path / "control"
        ),
        test_config=TestConfig(framework="pytest"),
    )

    result = agent.execute(database_session, "calculator-task")

    assert result.state is AgentRunState.FAILED
    assert result.gate_decision == "fail"
    assert result.evidence["test_exit_code"] != 0
    assert next(gate for gate in result.evidence["gate_results"] if gate["id"] == "G4_TEST")[
        "status"
    ] == "fail"
    assert database_session.get(TaskRecord, "calculator-task").status == "fail"


def test_missing_acceptance_criteria_blocks_task(
    tmp_path: Path, database_session: Session
) -> None:
    repository = tmp_path / "calculator"
    shutil.copytree(Path(__file__).parents[1] / "fixtures" / "calculator", repository)
    _make_git_repository(repository)
    _create_task(database_session, repository)
    task = database_session.get(TaskRecord, "calculator-task")
    assert task is not None
    task.acceptance_criteria_json = "[]"
    database_session.commit()
    agent = LocalCodingAgent(
        agent_id="local-agent-001",
        name="Local Coding Agent",
        runtime=FixtureRuntime(),
        workspace_manager=WorkspaceManager(
            tmp_path / "workspaces", control_repository=tmp_path / "control"
        ),
    )

    result = agent.execute(database_session, "calculator-task")

    assert result.state is AgentRunState.BLOCKED
    assert result.gate_decision == "blocked"
    assert database_session.get(TaskRecord, "calculator-task").status == "blocked"
