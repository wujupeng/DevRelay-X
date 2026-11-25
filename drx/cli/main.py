import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import select

from drx.agent.config import load_config
from drx.agent.local import LocalCodingAgent
from drx.hardware import discover_node_hardware
from drx.models.agent import AgentRecord
from drx.models.evidence import EvidenceRecord
from drx.models.gate import GateRecord
from drx.models.node import NodeEvidenceRecord, NodeRecord
from drx.models.project import ProjectRecord
from drx.models.requirement import RequirementRecord
from drx.models.run import RunRecord
from drx.models.task import TaskRecord
from drx.runtime.http import LocalHTTPRuntime
from drx.storage.database import SessionLocal, create_database
from drx.workspace.manager import WorkspaceManager

app = typer.Typer(help="DevRelay-X command line")
node_app = typer.Typer(help="Local node discovery and health")
runtime_app = typer.Typer(help="Configured model runtimes")
agent_app = typer.Typer(help="Local coding agents")
task_app = typer.Typer(help="Task management")
run_app = typer.Typer(help="AgentRun inspection")
gate_app = typer.Typer(help="Gate decisions")

app.add_typer(node_app, name="node")
app.add_typer(runtime_app, name="runtime")
app.add_typer(agent_app, name="agent")
app.add_typer(task_app, name="task")
app.add_typer(run_app, name="run")
app.add_typer(gate_app, name="gate")


@app.command()
def init() -> None:
    """Initialize the local SQLite database."""
    create_database()
    typer.echo("Initialized DevRelay-X at .drx/devrelay.sqlite")


@app.command()
def status() -> None:
    """Show the current local control-plane status."""
    database = Path(".drx/devrelay.sqlite")
    state = "initialized" if database.exists() else "not initialized"
    typer.echo(f"DevRelay-X v0.1: {state}")


@node_app.command("list")
def node_list() -> None:
    """Discover this node and persist a hardware evidence snapshot."""
    create_database()
    payload = discover_node_hardware()
    captured_at = datetime.now(UTC)
    with SessionLocal() as session:
        node = session.get(NodeRecord, "Node-001")
        if node is None:
            session.add(NodeRecord(id="Node-001", name="Local Node"))
        session.add(
            NodeEvidenceRecord(
                id=uuid.uuid4().hex,
                node_id="Node-001",
                captured_at=captured_at,
                payload_json=json.dumps(payload, ensure_ascii=True),
            )
        )
        session.commit()
    typer.echo(json.dumps({"node_id": "Node-001", **payload}, indent=2))


@node_app.command("health")
def node_health() -> None:
    """Report local hardware discovery status."""
    payload = discover_node_hardware()
    typer.echo(
        json.dumps(
            {
                "node_id": "Node-001",
                "status": "available",
                "gpu_available": payload["gpu"]["available"],
            },
            indent=2,
        )
    )


@runtime_app.command("list")
def runtime_list(
    config_path: Annotated[Path, typer.Option("--config")] = Path(".drx/config.yaml"),
) -> None:
    """Show the configured runtime identity."""
    config = load_config(config_path)
    runtime = LocalHTTPRuntime(config.runtime)
    try:
        typer.echo(json.dumps(runtime.identify(), indent=2))
    finally:
        runtime.shutdown()


@runtime_app.command("health")
def runtime_health(
    config_path: Annotated[Path, typer.Option("--config")] = Path(".drx/config.yaml"),
) -> None:
    """Check the configured local runtime."""
    config = load_config(config_path)
    runtime = LocalHTTPRuntime(config.runtime)
    try:
        available = runtime.health_check()
    finally:
        runtime.shutdown()
    typer.echo("runtime available" if available else "runtime unavailable")
    if not available:
        raise typer.Exit(code=1)


@agent_app.command("list")
def agent_list() -> None:
    """List registered Agent identities."""
    create_database()
    with SessionLocal() as session:
        agents = session.scalars(select(AgentRecord).order_by(AgentRecord.id)).all()
    typer.echo(json.dumps([{"id": agent.id, "name": agent.name} for agent in agents], indent=2))


@agent_app.command("run")
def agent_run(
    task_id: str,
    branch: Annotated[str | None, typer.Option("--branch")] = None,
    config_path: Annotated[Path, typer.Option("--config")] = Path(".drx/config.yaml"),
) -> None:
    """Execute a task in an isolated local workspace."""
    create_database()
    config = load_config(config_path)
    control_root = _control_repository_root()
    runtime = LocalHTTPRuntime(config.runtime)
    agent = LocalCodingAgent(
        agent_id=config.agent.id,
        name=config.agent.name,
        runtime=runtime,
        workspace_manager=WorkspaceManager(
            config.workspace.root,
            control_repository=control_root,
            allow_control_repository=config.workspace.allow_control_repository,
        ),
        test_config=config.test,
    )
    try:
        with SessionLocal() as session:
            result = agent.execute(session, task_id, branch=branch)
    finally:
        agent.stop()
    typer.echo(
        json.dumps(
            {
                "run_id": result.run_id,
                "state": result.state.value,
                "gate_decision": result.gate_decision,
                "evidence": result.evidence,
            },
            indent=2,
        )
    )
    if result.gate_decision != "pass":
        raise typer.Exit(code=1)


@task_app.command("create")
def task_create(
    task_id: str,
    project_id: str = typer.Option(..., "--project-id"),
    project_name: str = typer.Option(..., "--project-name"),
    repository: str = typer.Option(..., "--repository"),
    title: str = typer.Option(..., "--title"),
    objective: str = typer.Option(..., "--objective"),
    acceptance_criteria: Annotated[list[str] | None, typer.Option("--acceptance-criterion")] = None,
) -> None:
    """Create project, requirement, and executable task records."""
    if not acceptance_criteria:
        raise typer.BadParameter("provide at least one --acceptance-criterion")
    create_database()
    requirement_id = uuid.uuid4().hex
    with SessionLocal() as session:
        if session.get(TaskRecord, task_id) is not None:
            raise typer.BadParameter(f"task already exists: {task_id}")
        project = session.get(ProjectRecord, project_id)
        if project is None:
            session.add(
                ProjectRecord(
                    id=project_id,
                    name=project_name,
                    repository=repository,
                    description="",
                )
            )
            session.flush()
        elif project.repository != repository:
            raise typer.BadParameter(
                f"project {project_id} already uses a different repository"
            )
        session.add(
            RequirementRecord(
                id=requirement_id,
                project_id=project_id,
                title=title,
                description=objective,
                status="approved",
            )
        )
        session.add(
            TaskRecord(
                id=task_id,
                requirement_id=requirement_id,
                title=title,
                objective=objective,
                acceptance_criteria_json=json.dumps(acceptance_criteria),
            )
        )
        session.commit()
    typer.echo(f"Created task {task_id}")


@run_app.command("show")
def run_show(run_id: str) -> None:
    """Show the persisted AgentRun state."""
    create_database()
    with SessionLocal() as session:
        record = session.get(RunRecord, run_id)
        if record is None:
            raise typer.BadParameter(f"unknown run: {run_id}")
        payload = {
            "id": record.id,
            "task_id": record.task_id,
            "agent_id": record.agent_id,
            "runtime_id": record.runtime_id,
            "status": record.status,
            "workspace": record.workspace,
            "started_at": record.started_at.isoformat() if record.started_at else None,
            "ended_at": record.ended_at.isoformat() if record.ended_at else None,
            "failure_reason": record.failure_reason,
        }
    typer.echo(json.dumps(payload, indent=2))


@run_app.command("evidence")
def run_evidence(run_id: str) -> None:
    """Show the persisted evidence for an AgentRun."""
    create_database()
    with SessionLocal() as session:
        record = session.scalars(
            select(EvidenceRecord).where(EvidenceRecord.run_id == run_id)
        ).first()
        if record is None:
            raise typer.BadParameter(f"evidence not found for run: {run_id}")
        payload = json.loads(record.metadata_json)
    typer.echo(json.dumps(payload, indent=2))


@gate_app.command("show")
def gate_show(run_id: str) -> None:
    """Show Gate Engine results for an AgentRun."""
    create_database()
    with SessionLocal() as session:
        gates = session.scalars(
            select(GateRecord)
            .where(GateRecord.run_id == run_id)
            .order_by(GateRecord.id)
        ).all()
        evidence = session.scalars(
            select(EvidenceRecord).where(EvidenceRecord.run_id == run_id)
        ).first()
    if not gates:
        raise typer.BadParameter(f"gate results not found for run: {run_id}")
    typer.echo(
        json.dumps(
            {
                "run_id": run_id,
                "decision": evidence.result if evidence else "blocked",
                "gates": [
                    {
                        "id": gate.id.rsplit(":", maxsplit=1)[-1],
                        "decision": gate.decision,
                        "reason": gate.reason,
                    }
                    for gate in gates
                ],
            },
            indent=2,
        )
    )


def _control_repository_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode:
        raise RuntimeError("run `drx agent run` from the DevRelay-X control repository")
    return Path(result.stdout.strip()).resolve()


if __name__ == "__main__":
    app()
