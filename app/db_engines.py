"""
Hardened, read-only database access for user-supplied databases.

User databases are stored as SQLite files. Because the content of an upload
is untrusted, every connection made to one goes through
``open_readonly_connection`` which applies defence in depth:

1. The file is opened with ``mode=ro`` (the OS-level handle is read-only).
2. ``PRAGMA query_only`` and ``trusted_schema=OFF`` are enabled.
3. A SQLite *authorizer* whitelists what the engine may do. Anything that is
   not a SELECT/READ/whitelisted function/whitelisted read-only PRAGMA is
   denied by SQLite itself, no matter what SQL text the LLM produced.
4. ``ATTACH`` is disabled via ``SQLITE_LIMIT_ATTACHED = 0``.
5. Blob / SQL / expression sizes are capped.
6. A progress handler aborts queries that exceed a wall-clock deadline.

This module only depends on the standard library at import time.
SQLAlchemy is imported lazily inside ``make_sqlite_engine``.
"""

from __future__ import annotations

from app import env_config  # noqa: F401  (loads .env first)

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

SQLITE_MAGIC = b"SQLite format 3\x00"

# Wall-clock limit for a single connection's work (seconds).
QUERY_TIMEOUT_SECONDS = float(os.getenv("QC_QUERY_TIMEOUT_SECONDS", "10"))

# Hard cap on rows returned to the caller.
MAX_RESULT_ROWS = int(os.getenv("QC_MAX_RESULT_ROWS", "1000"))

# Largest single value (bytes) SQLite may build (guards zeroblob/replace bombs).
MAX_VALUE_BYTES = int(os.getenv("QC_MAX_VALUE_BYTES", "1000000"))

# Read-only PRAGMAs needed for schema introspection (SQLAlchemy inspector).
_ALLOWED_PRAGMAS = frozenset(
    {
        "table_info",
        "table_xinfo",
        "foreign_key_list",
        "index_list",
        "index_info",
        "index_xinfo",
        "database_list",
        "read_uncommitted",
    }
)

# Scalar functions that must never run on untrusted data.
_DENIED_FUNCTIONS = frozenset(
    {
        "load_extension",
        "readfile",
        "writefile",
        "edit",
        "fts3_tokenizer",
    }
)


class QueryTimeout(Exception):
    """Raised when a query is interrupted by the deadline."""


def _make_authorizer() -> Callable[..., int]:
    ok = sqlite3.SQLITE_OK
    deny = sqlite3.SQLITE_DENY

    allow_actions = {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_RECURSIVE,
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_SAVEPOINT,
    }

    def authorizer(action, arg1, arg2, db_name, source):  # noqa: ANN001
        if action in allow_actions:
            return ok

        if action == sqlite3.SQLITE_FUNCTION:
            if arg2 and str(arg2).lower() in _DENIED_FUNCTIONS:
                return deny
            return ok

        if action == sqlite3.SQLITE_PRAGMA:
            if arg1 and str(arg1).lower() in _ALLOWED_PRAGMAS:
                return ok
            return deny

        # INSERT/UPDATE/DELETE/CREATE/DROP/ALTER/ATTACH/DETACH/REINDEX/...
        return deny

    return authorizer


def open_readonly_connection(
    path: str | os.PathLike[str],
    timeout: float | None = None,
) -> sqlite3.Connection:
    """Open ``path`` as a hardened read-only SQLite connection."""

    resolved = Path(path).resolve()

    if not resolved.is_file():
        raise FileNotFoundError(f"Database file not found: {resolved.name}")

    uri = resolved.as_uri() + "?mode=ro"

    conn = sqlite3.connect(
        uri,
        uri=True,
        check_same_thread=False,
        timeout=5.0,
    )

    try:
        # PRAGMAs first: the authorizer below denies them.
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA trusted_schema = OFF")

        _apply_limits(conn)

        conn.set_authorizer(_make_authorizer())

        seconds = QUERY_TIMEOUT_SECONDS if timeout is None else timeout
        deadline = time.monotonic() + seconds

        def progress() -> int:
            return 1 if time.monotonic() > deadline else 0

        conn.set_progress_handler(progress, 10_000)

    except Exception:
        conn.close()
        raise

    return conn


def _apply_limits(conn: sqlite3.Connection) -> None:
    """Apply SQLite run-time limits (Python 3.11+)."""

    if not hasattr(conn, "setlimit"):
        return

    limits = {
        "SQLITE_LIMIT_LENGTH": MAX_VALUE_BYTES,
        "SQLITE_LIMIT_SQL_LENGTH": 50_000,
        "SQLITE_LIMIT_EXPR_DEPTH": 200,
        "SQLITE_LIMIT_COMPOUND_SELECT": 20,
        "SQLITE_LIMIT_ATTACHED": 0,
        "SQLITE_LIMIT_FUNCTION_ARG": 32,
    }

    for name, value in limits.items():
        constant = getattr(sqlite3, name, None)

        if constant is not None:
            conn.setlimit(constant, value)


def run_readonly_query(
    path: str | os.PathLike[str],
    sql: str,
    max_rows: int | None = None,
    timeout: float | None = None,
) -> tuple[list[str], list[tuple[Any, ...]], bool]:
    """
    Execute a single query against a user database.

    Returns ``(column_names, rows, truncated)``. At most ``max_rows`` rows are
    returned; ``truncated`` tells the caller whether more existed.
    """

    limit = MAX_RESULT_ROWS if max_rows is None else max_rows

    conn = open_readonly_connection(path, timeout=timeout)

    try:
        cursor = conn.execute(sql)
        fetched = cursor.fetchmany(limit + 1)
        columns = [d[0] for d in (cursor.description or [])]

    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower():
            raise QueryTimeout(
                "Query exceeded the time limit and was cancelled."
            ) from exc
        raise

    finally:
        conn.close()

    truncated = len(fetched) > limit

    return columns, fetched[:limit], truncated


# ---------------------------------------------------------------------------
# SQLAlchemy integration
# ---------------------------------------------------------------------------

_ENGINE_CACHE: dict[str, Any] = {}


def make_sqlite_engine(path: str | os.PathLike[str]):
    """
    Return a SQLAlchemy engine whose every connection is hardened.

    ``NullPool`` guarantees a fresh connection (and therefore a fresh
    deadline) for each ``engine.connect()``.
    """

    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    key = str(Path(path).resolve())

    engine = _ENGINE_CACHE.get(key)

    if engine is None:
        engine = create_engine(
            "sqlite://",
            creator=lambda: open_readonly_connection(key),
            poolclass=NullPool,
        )
        _ENGINE_CACHE[key] = engine

    return engine


def forget_engine(path: str | os.PathLike[str]) -> None:
    """Drop the cached engine for a file that is about to be deleted."""

    key = str(Path(path).resolve())
    engine = _ENGINE_CACHE.pop(key, None)

    if engine is not None:
        engine.dispose()
