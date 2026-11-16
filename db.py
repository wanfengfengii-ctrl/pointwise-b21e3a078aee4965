"""SQLite transaction boundary shared by business owners."""
from contextlib import contextmanager
import sqlite3


SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS models_v1(
        id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL,
        PRIMARY KEY(id,revision))""",
    """CREATE TABLE IF NOT EXISTS heads_v1(
        id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
        FOREIGN KEY(id,revision) REFERENCES models_v1(id,revision))""",
    """CREATE TABLE IF NOT EXISTS receipts_v1(
        request_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
        model_id TEXT NOT NULL, revision INTEGER NOT NULL,
        FOREIGN KEY(model_id,revision) REFERENCES models_v1(id,revision))""",
    """CREATE TABLE IF NOT EXISTS tasks_v1(
        task_id TEXT PRIMARY KEY, model_id TEXT NOT NULL, revision INTEGER NOT NULL,
        payload TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('saved','success','impossible')),
        FOREIGN KEY(model_id,revision) REFERENCES models_v1(id,revision))""",
    """CREATE TABLE IF NOT EXISTS results_v1(
        task_id TEXT PRIMARY KEY REFERENCES tasks_v1(task_id),
        status TEXT NOT NULL CHECK(status IN ('success','impossible')), payload TEXT NOT NULL)"""
)


@contextmanager
def atomic(connection, before_commit=None):
    """Single worker, non-nested transaction; DDL is rolled back too."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        if before_commit is not None:
            before_commit()
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def connect(path):
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=1000")
    return connection


def version(connection):
    """Current storage version: '1' (SDK1 tables) or '2' (SDK2 tables)."""
    row = connection.execute("SELECT value FROM meta WHERE key='version'").fetchone()
    return None if row is None else row[0]


def initialize(connection):
    with atomic(connection):
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
        if exists:
            current = version(connection)
            if current and current not in ("1", "2"):
                raise ValueError("unsupported archive version")
        for statement in SCHEMA:
            connection.execute(statement)
        connection.execute("INSERT OR IGNORE INTO meta VALUES('version','1')")
