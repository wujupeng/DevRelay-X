import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from drx.core.gate_authority import verify_gate_approval


class Base(DeclarativeBase):
    pass


def get_database_url(database_path: str | Path = ".drx/devrelay.sqlite") -> str:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path.as_posix()}"


engine = create_engine(get_database_url(), future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def create_database(bind: Engine = engine) -> None:
    # Register every SQLAlchemy model before creating tables.
    for module_name in (
        "agent",
        "evidence",
        "gate",
        "node",
        "project",
        "relay",
        "requirement",
        "run",
        "task",
    ):
        __import__(f"drx.models.{module_name}")

    _register_gate_approval_function(bind)
    Base.metadata.create_all(bind)
    _upgrade_existing_database(bind)
    _create_task_status_trigger(bind)


def _upgrade_existing_database(bind: Engine) -> None:
    additions = {
        "tasks": {
            "objective": "TEXT NOT NULL DEFAULT ''",
            "acceptance_criteria_json": "TEXT NOT NULL DEFAULT '[]'",
        },
        "runs": {
            "runtime_id": "VARCHAR(200) NOT NULL DEFAULT ''",
            "workspace": "VARCHAR(1000) NOT NULL DEFAULT ''",
            "started_at": "DATETIME",
            "ended_at": "DATETIME",
            "initial_commit": "VARCHAR(64) NOT NULL DEFAULT ''",
            "final_commit": "VARCHAR(64) NOT NULL DEFAULT ''",
            "evidence_json": "TEXT NOT NULL DEFAULT '{}'",
            "failure_reason": "TEXT NOT NULL DEFAULT ''",
        },
        "evidence": {
            "run_id": "VARCHAR(64)",
            "verification_digest": "VARCHAR(64) NOT NULL DEFAULT ''",
            "approval_signature": "VARCHAR(64) NOT NULL DEFAULT ''",
        },
        "gate_decisions": {"run_id": "VARCHAR(64)"},
    }
    with bind.begin() as connection:
        existing_tables = set(inspect(bind).get_table_names())
        for table_name, columns in additions.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {
                column["name"] for column in inspect(bind).get_columns(table_name)
            }
            for column_name, declaration in columns.items():
                if column_name not in existing_columns:
                    connection.execute(
                        text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {declaration}')
                    )


def _register_gate_approval_function(bind: Engine) -> None:
    if bind.dialect.name == "sqlite" and not event.contains(
        bind, "checkout", _register_sqlite_gate_approval
    ):
        event.listen(bind, "checkout", _register_sqlite_gate_approval)


def _register_sqlite_gate_approval(
    dbapi_connection: object,
    _connection_record: object,
    _connection_proxy: object,
) -> None:
    if not isinstance(dbapi_connection, sqlite3.Connection):
        raise TypeError("SQLite Gate approval validator requires a SQLite connection")
    dbapi_connection.create_function(
        "drx_verify_gate_approval",
        13,
        verify_gate_approval,
        deterministic=True,
    )


def _create_task_status_trigger(bind: Engine) -> None:
    if bind.dialect.name != "sqlite":
        return
    with bind.begin() as connection:
        for trigger_name in (
            "enforce_task_status_transition",
            "reject_task_pass_insert",
            "reject_passed_run_updates",
            "reject_passed_run_deletes",
            "reject_passed_evidence_updates",
            "reject_passed_evidence_deletes",
            "reject_passed_gate_inserts",
            "reject_passed_gate_updates",
            "reject_passed_gate_deletes",
        ):
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger_name}")
        connection.exec_driver_sql(
            """
                CREATE TRIGGER enforce_task_status_transition
                BEFORE UPDATE OF status ON tasks
                FOR EACH ROW
                WHEN NEW.status != OLD.status
                AND NOT (
                    (OLD.status = 'created' AND NEW.status IN
                        ('assigned', 'in_progress', 'blocked', 'cancelled'))
                    OR (OLD.status = 'assigned' AND NEW.status IN
                        ('in_progress', 'blocked', 'cancelled'))
                    OR (OLD.status = 'in_progress' AND NEW.status IN
                        ('implemented', 'fail', 'blocked', 'cancelled'))
                    OR (OLD.status = 'implemented' AND NEW.status IN
                        ('verifying', 'fail', 'blocked'))
                    OR (OLD.status = 'verifying' AND NEW.status IN
                        ('reviewing', 'fail', 'blocked'))
                    OR (OLD.status = 'reviewing' AND NEW.status IN
                        ('verifying', 'fail', 'blocked'))
                    OR (
                        OLD.status = 'verifying'
                        AND NEW.status = 'pass'
                        AND EXISTS (
                            SELECT 1
                            FROM evidence AS e
                            JOIN runs AS r ON r.id = e.run_id
                            WHERE e.task_id = NEW.id
                              AND e.type = 'execution'
                              AND e.producer = r.agent_id
                              AND e.result = 'pass'
                              AND r.status = 'completed'
                              AND r.evidence_json = e.metadata_json
                              AND drx_verify_gate_approval(
                                  e.task_id,
                                  e.run_id,
                                  r.agent_id,
                                  r.status,
                                  r.initial_commit,
                                  r.final_commit,
                                  r.workspace,
                                  e.type,
                                  e.producer,
                                  e.result,
                                  e.verification_digest,
                                  e.metadata_json,
                                  e.approval_signature
                              ) = 1
                              AND (
                                  SELECT COUNT(*)
                                  FROM gate_decisions AS g
                                  WHERE g.task_id = e.task_id
                                    AND g.run_id = e.run_id
                              ) = 7
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM gate_decisions AS g
                                  WHERE g.task_id = e.task_id
                                    AND g.run_id = e.run_id
                                    AND (
                                        g.decision != 'pass'
                                        OR g.id NOT IN (
                                            e.run_id || ':G0_ENVIRONMENT',
                                            e.run_id || ':G1_WORKSPACE',
                                            e.run_id || ':G2_TASK',
                                            e.run_id || ':G3_EXECUTION',
                                            e.run_id || ':G4_TEST',
                                            e.run_id || ':G5_GIT',
                                            e.run_id || ':G6_EVIDENCE'
                                        )
                                    )
                              )
                        )
                    )
                )
                BEGIN
                    SELECT RAISE(ABORT, 'illegal or unverified Task status transition');
                END
                """
        )
        connection.exec_driver_sql(
            """
                CREATE TRIGGER reject_task_pass_insert
                BEFORE INSERT ON tasks
                FOR EACH ROW
                WHEN NEW.status = 'pass'
                BEGIN
                    SELECT RAISE(ABORT, 'Task PASS requires verified Gate evidence');
                END
                """
        )
        connection.exec_driver_sql(
            """
                CREATE TRIGGER reject_passed_run_updates
                BEFORE UPDATE ON runs
                FOR EACH ROW
                WHEN EXISTS (
                    SELECT 1 FROM tasks
                    WHERE id = OLD.task_id AND status = 'pass'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'cannot modify a passed Task run');
                END
                """
        )
        connection.exec_driver_sql(
            """
                CREATE TRIGGER reject_passed_run_deletes
                BEFORE DELETE ON runs
                FOR EACH ROW
                WHEN EXISTS (
                    SELECT 1 FROM tasks
                    WHERE id = OLD.task_id AND status = 'pass'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'cannot delete a passed Task run');
                END
                """
        )
        connection.exec_driver_sql(
            """
                CREATE TRIGGER reject_passed_evidence_updates
                BEFORE UPDATE ON evidence
                FOR EACH ROW
                WHEN EXISTS (
                    SELECT 1 FROM tasks
                    WHERE id = OLD.task_id AND status = 'pass'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'cannot modify evidence for a passed Task');
                END
                """
        )
        connection.exec_driver_sql(
            """
                CREATE TRIGGER reject_passed_evidence_deletes
                BEFORE DELETE ON evidence
                FOR EACH ROW
                WHEN EXISTS (
                    SELECT 1 FROM tasks
                    WHERE id = OLD.task_id AND status = 'pass'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'cannot delete evidence for a passed Task');
                END
                """
        )
        connection.exec_driver_sql(
            """
                CREATE TRIGGER reject_passed_gate_inserts
                BEFORE INSERT ON gate_decisions
                FOR EACH ROW
                WHEN EXISTS (
                    SELECT 1 FROM tasks
                    WHERE id = NEW.task_id AND status = 'pass'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'cannot add Gates to a passed Task');
                END
                """
        )
        connection.exec_driver_sql(
            """
                CREATE TRIGGER reject_passed_gate_updates
                BEFORE UPDATE ON gate_decisions
                FOR EACH ROW
                WHEN EXISTS (
                    SELECT 1 FROM tasks
                    WHERE id IN (OLD.task_id, NEW.task_id) AND status = 'pass'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'cannot modify Gates for a passed Task');
                END
                """
        )
        connection.exec_driver_sql(
            """
                CREATE TRIGGER reject_passed_gate_deletes
                BEFORE DELETE ON gate_decisions
                FOR EACH ROW
                WHEN EXISTS (
                    SELECT 1 FROM tasks
                    WHERE id = OLD.task_id AND status = 'pass'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'cannot delete Gates for a passed Task');
                END
                """
        )


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
