import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlmodel import SQLModel, Session, create_engine

DATABASE_URL = os.getenv("LABORLINK_DB_URL", "sqlite:///./laborlink.db").strip()
if not DATABASE_URL:
    raise RuntimeError("LABORLINK_DB_URL must not be empty")

# SQLite creates the file but not its parent directory. Create only the
# configured parent; never replace a bad production path with a temporary DB.
database_url = make_url(DATABASE_URL)
if database_url.drivername.startswith("sqlite") and database_url.database not in (None, ":memory:"):
    Path(database_url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def _add_column_if_missing(connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))


def create_db_and_tables() -> None:
    """Create new tables and make only additive migrations for the original demo DB."""
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        for column, definition in {"account_id": "INTEGER", "region": "TEXT NOT NULL DEFAULT ''", "location_consent": "BOOLEAN NOT NULL DEFAULT 0"}.items():
            _add_column_if_missing(connection, "worker", column, definition)
        for column, definition in {"employer_account_id": "INTEGER", "region": "TEXT NOT NULL DEFAULT ''", "latitude": "REAL", "longitude": "REAL", "is_open": "BOOLEAN NOT NULL DEFAULT 1"}.items():
            _add_column_if_missing(connection, "job", column, definition)
        _add_column_if_missing(connection, "jobapplication", "worker_id", "INTEGER")
        connection.execute(text("UPDATE jobapplication SET worker_id = worker_account_id WHERE worker_id IS NULL"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_jobapplication_job_worker_unique ON jobapplication(job_id, worker_id)"))


def get_session():
    with Session(engine) as session:
        yield session
