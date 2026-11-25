from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


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

    Base.metadata.create_all(bind)
    _upgrade_existing_database(bind)


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
        "evidence": {"run_id": "VARCHAR(64)"},
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
