import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from drx.agent.config import TestConfig
from drx.agent.local import LocalCodingAgent
from drx.agent.run import AgentRunState
from drx.models.project import ProjectRecord
from drx.models.requirement import RequirementRecord
from drx.models.run import RunRecord
from drx.models.task import TaskRecord
from drx.policy.engine import PolicyEngine
from drx.runtime.config import RuntimeConfig
from drx.runtime.http import LocalHTTPRuntime
from drx.storage.database import create_database
from drx.workspace.manager import WorkspaceManager


@pytest.mark.skipif(
    not os.environ.get("DRX_LIVE_RUNTIME_ENDPOINT")
    or not os.environ.get("DRX_LIVE_MODEL_ID"),
    reason="set DRX_LIVE_RUNTIME_ENDPOINT and DRX_LIVE_MODEL_ID for live model E2E",
)
def test_live_model_agent_to_gate(tmp_path: Path) -> None:
    repository = tmp_path / "calculator"
    shutil.copytree(Path(__file__).parents[1] / "fixtures" / "calculator", repository)
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=repository, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "DevRelay live E2E"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "devrelay-live-e2e@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-m", "calculator live E2E fixture"],
        cwd=repository,
        check=True,
        capture_output=True,
    )

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_database(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session: Session = session_factory()
    runtime = LocalHTTPRuntime(
        RuntimeConfig(
            endpoint=os.environ["DRX_LIVE_RUNTIME_ENDPOINT"],
            model=os.environ["DRX_LIVE_MODEL_ID"],
            timeout_seconds=600,
            max_tokens=2048,
            temperature=0.0,
        )
    )
    try:
        session.add(
            ProjectRecord(
                id="live-calculator-project",
                name="Live Calculator",
                repository=str(repository),
                description="Isolated real-model acceptance fixture",
            )
        )
        session.flush()
        session.add(
            RequirementRecord(
                id="live-calculator-requirement",
                project_id="live-calculator-project",
                title="Calculator functions",
                description="Implement arithmetic",
                status="approved",
            )
        )
        session.flush()
        session.add(
            TaskRecord(
                id="live-calculator-task",
                requirement_id="live-calculator-requirement",
                title="Implement calculator functions",
                objective=(
                    "In calculator.py, implement both functions: return left + right "
                    "from add and return left - right from subtract. Change both "
                    "functions and no others."
                ),
                acceptance_criteria_json=json.dumps(
                    [
                        "add returns the sum",
                        "subtract returns the difference",
                        "neither function returns None",
                    ]
                ),
            )
        )
        session.commit()

        agent = LocalCodingAgent(
            agent_id="live-local-agent",
            name="Live Local Coding Agent",
            runtime=runtime,
            workspace_manager=WorkspaceManager(
                tmp_path / "workspaces",
                control_repository=Path(__file__).parents[2],
            ),
            test_config=TestConfig(framework="pytest", timeout_seconds=180),
            policy_engine=PolicyEngine(
                allowed_actions=(
                    PolicyEngine.DEFAULT_ALLOWED_ACTIONS | PolicyEngine.HIGH_RISK_ACTIONS
                )
            ),
        )
        result = agent.execute(session, "live-calculator-task")
        evidence = result.evidence

        assert result.state is AgentRunState.COMPLETED, evidence["failure_reason"]
        assert result.gate_decision == "pass"
        assert evidence["changed_files"] == ["calculator.py"]
        assert evidence["test_exit_code"] == 0
        assert evidence["commit_created"]
        assert evidence["initial_commit"] != evidence["final_commit"]
        gate_results = evidence["gate_results"]
        assert {gate["id"] for gate in gate_results} == {
            "G0_ENVIRONMENT",
            "G1_WORKSPACE",
            "G2_TASK",
            "G3_EXECUTION",
            "G4_TEST",
            "G5_GIT",
            "G6_EVIDENCE",
        }
        assert all(gate["status"] == "pass" for gate in gate_results)
        task = session.get(TaskRecord, "live-calculator-task")
        run = session.get(RunRecord, result.run_id)
        assert task is not None and task.status == "pass"
        assert run is not None and run.final_commit == evidence["final_commit"]
    finally:
        runtime.shutdown()
        session.close()
        engine.dispose()
