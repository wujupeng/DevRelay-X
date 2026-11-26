import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from drx.core.gate_authority import _issue_gate_approval
from drx.models.agent import AgentRecord
from drx.models.evidence import EvidenceRecord
from drx.models.project import ProjectRecord
from drx.models.requirement import RequirementRecord
from drx.models.run import RunRecord
from drx.models.task import TaskRecord
from drx.storage.database import create_database


@pytest.fixture
def database_session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect():
        pass
    create_database(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _insert_passing_records(session: Session, *, issue_signature: bool) -> None:
    session.add(
        ProjectRecord(
            id="authority-project",
            name="Authority fixture",
            repository="unused",
            description="Database authority regression fixture",
        )
    )
    session.flush()
    session.add(
        RequirementRecord(
            id="authority-requirement",
            project_id="authority-project",
            title="Authority fixture",
            description="Database authority regression fixture",
            status="approved",
        )
    )
    session.flush()
    session.add(
        TaskRecord(
            id="authority-task",
            requirement_id="authority-requirement",
            title="Authority fixture",
            status="created",
        )
    )
    session.add(
        AgentRecord(
            id="authority-agent",
            name="Fixture Agent",
            adapter_type="test",
        )
    )
    session.flush()

    run = RunRecord(
        id="authority-run",
        task_id="authority-task",
        agent_id="authority-agent",
        status="completed",
        runtime_id="test-runtime",
        workspace="managed-workspace",
        initial_commit="a" * 40,
        final_commit="b" * 40,
    )
    gate_results = [
        {"id": gate_id, "status": "pass", "reason": "verified"}
        for gate_id in (
            "G0_ENVIRONMENT",
            "G1_WORKSPACE",
            "G2_TASK",
            "G3_EXECUTION",
            "G4_TEST",
            "G5_GIT",
            "G6_EVIDENCE",
        )
    ]
    evidence = EvidenceRecord(
        id="authority-evidence",
        task_id="authority-task",
        run_id="authority-run",
        type="execution",
        producer="authority-agent",
        result="pass",
        verification_digest="d" * 64,
        metadata_json=json.dumps(
            {
                "task_id": "authority-task",
                "run_id": "authority-run",
                "agent_id": "authority-agent",
                "execution_result": "pass",
                "gate_decision": "pass",
                "gate_results": gate_results,
                "verification_digest": "d" * 64,
                "initial_commit": run.initial_commit,
                "final_commit": run.final_commit,
                "workspace": run.workspace,
                "commit_created": True,
                "test_exit_code": 0,
            }
        ),
    )
    run.evidence_json = evidence.metadata_json
    if issue_signature:
        evidence.approval_signature = _issue_gate_approval(
            task_id=evidence.task_id,
            run_id=run.id,
            agent_id=run.agent_id,
            run_status=run.status,
            initial_commit=run.initial_commit,
            final_commit=run.final_commit,
            workspace=run.workspace,
            evidence_type=evidence.type,
            evidence_producer=evidence.producer,
            evidence_result=evidence.result,
            verification_digest=evidence.verification_digest,
            metadata_json=evidence.metadata_json,
        )
    session.execute(
        text(
            "INSERT INTO runs "
            "(id, task_id, agent_id, status, runtime_id, workspace, started_at, "
            "ended_at, initial_commit, final_commit, evidence_json, failure_reason) "
            "VALUES (:id, :task_id, :agent_id, :status, :runtime_id, :workspace, "
            ":started_at, :ended_at, :initial_commit, :final_commit, :evidence_json, "
            ":failure_reason)"
        ),
        {
            "id": run.id,
            "task_id": run.task_id,
            "agent_id": run.agent_id,
            "status": run.status,
            "runtime_id": run.runtime_id,
            "workspace": run.workspace,
            "started_at": None,
            "ended_at": None,
            "initial_commit": run.initial_commit,
            "final_commit": run.final_commit,
            "evidence_json": run.evidence_json,
            "failure_reason": "",
        },
    )
    session.execute(
        text(
            "INSERT INTO evidence "
            "(id, task_id, run_id, type, producer, result, metadata_json, "
            "verification_digest, approval_signature) "
            "VALUES (:id, :task_id, :run_id, :type, :producer, :result, "
            ":metadata_json, :verification_digest, :approval_signature)"
        ),
        {
            "id": evidence.id,
            "task_id": evidence.task_id,
            "run_id": evidence.run_id,
            "type": evidence.type,
            "producer": evidence.producer,
            "result": evidence.result,
            "metadata_json": evidence.metadata_json,
            "verification_digest": evidence.verification_digest,
            "approval_signature": evidence.approval_signature or "",
        },
    )
    session.execute(
        text(
            "INSERT INTO gate_decisions (id, task_id, run_id, decision, reason) "
            "VALUES (:id, :task_id, :run_id, :decision, :reason)"
        ),
        [
            {
                "id": f"authority-run:{gate['id']}",
                "task_id": "authority-task",
                "run_id": "authority-run",
                "decision": "pass",
                "reason": "verified",
            }
            for gate in gate_results
        ],
    )
    session.flush()
    for status in ("in_progress", "implemented", "verifying"):
        session.execute(
            text("UPDATE tasks SET status = :status WHERE id = 'authority-task'"),
            {"status": status},
        )
    session.commit()


def test_direct_sql_pass_without_gate_credential_is_rejected(
    database_session: Session,
) -> None:
    _insert_passing_records(database_session, issue_signature=False)

    with pytest.raises(IntegrityError, match="illegal or unverified Task status transition"):
        database_session.execute(
            text("UPDATE tasks SET status = 'pass' WHERE id = 'authority-task'")
        )


def test_direct_sql_cannot_insert_task_as_pass(database_session: Session) -> None:
    database_session.add(
        ProjectRecord(
            id="insert-project",
            name="Insert fixture",
            repository="unused",
            description="Direct SQL Task insertion fixture",
        )
    )
    database_session.flush()
    database_session.add(
        RequirementRecord(
            id="insert-requirement",
            project_id="insert-project",
            title="Insert fixture",
            description="Direct SQL Task insertion fixture",
            status="approved",
        )
    )
    database_session.flush()

    with pytest.raises(IntegrityError, match="Task PASS requires verified Gate"):
        database_session.execute(
            text(
                "INSERT INTO tasks "
                "(id, requirement_id, title, status, objective, acceptance_criteria_json) "
                "VALUES ('forged-task', 'insert-requirement', 'Forged', 'pass', '', '[]')"
            )
        )


@pytest.mark.parametrize("tamper", ["evidence", "final_commit", "unknown_gate"])
def test_database_rejects_tampered_signed_gate_records(
    database_session: Session, tamper: str
) -> None:
    _insert_passing_records(database_session, issue_signature=True)

    if tamper == "evidence":
        database_session.execute(
            text(
                "UPDATE evidence SET metadata_json = '{\"tampered\": true}' "
                "WHERE id = 'authority-evidence'"
            )
        )
    elif tamper == "final_commit":
        database_session.execute(
            text("UPDATE runs SET final_commit = :commit WHERE id = 'authority-run'"),
            {"commit": "c" * 40},
        )
    else:
        database_session.execute(
            text(
                "INSERT INTO gate_decisions (id, task_id, run_id, decision, reason) "
                "VALUES ('authority-run:G7_FORGED', 'authority-task', "
                "'authority-run', 'pass', 'unregistered')"
            )
        )
    database_session.commit()

    with pytest.raises(IntegrityError, match="illegal or unverified Task status transition"):
        database_session.execute(
            text("UPDATE tasks SET status = 'pass' WHERE id = 'authority-task'")
        )
