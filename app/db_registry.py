"""
Registry of databases known to QueryClarify.

* Built-in databases (the 157 Spider databases in MySQL) are NOT stored here.
  Any valid database name that is not a user-database id is treated as
  built-in, exactly as before.
* User databases (uploaded by a client) are stored as SQLite files and
  tracked in ``<data>/registry.db`` together with their owner, size, status
  and expiry.

Ownership model
---------------
There is no login system yet, so a browser generates a random client id
(kept in ``localStorage``) and sends it as the ``X-Client-Id`` header. The
server only ever stores a salted SHA-256 hash of it. Whoever holds the id
owns the databases. Replace ``hash_owner`` with your real user id once you
add authentication; nothing else needs to change.

Only the standard library is used so this module is safe to import anywhere.
"""

from __future__ import annotations

from app import env_config  # noqa: F401  (loads .env first)

import hashlib
import hmac
import os
import re
import logging
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = Path(os.getenv("QC_DATA_DIR", PROJECT_ROOT / "data")).resolve()
USER_DB_DIR = DATA_DIR / "user_dbs"
REGISTRY_PATH = DATA_DIR / "registry.db"

# Built-in (MySQL / Spider) databases can be switched off for SQLite-only
# deployments.
BUILTIN_ENABLED = os.getenv("QC_ENABLE_BUILTIN_DBS", "true").lower() != "false"

# Quotas / retention.
MAX_DATABASES_PER_OWNER = int(os.getenv("QC_MAX_DATABASES_PER_OWNER", "5"))
MAX_BYTES_PER_OWNER = int(
    os.getenv("QC_MAX_BYTES_PER_OWNER", str(200 * 1024 * 1024))
)
# 0 disables expiry.
TTL_DAYS = float(os.getenv("QC_USER_DB_TTL_DAYS", "7"))

_DEFAULT_OWNER_SALT = "queryclarify-dev-salt-change-me"  # noqa: secret (public dev default, not a credential)
_OWNER_SALT = os.getenv("QC_OWNER_SALT", _DEFAULT_OWNER_SALT)


def owner_salt_is_default() -> bool:
    """True when QC_OWNER_SALT is unset, i.e. the public default salt is used."""

    return _OWNER_SALT == _DEFAULT_OWNER_SALT

USER_DB_ID_RE = re.compile(r"^u_[0-9a-f]{12}$")
BUILTIN_NAME_RE = re.compile(r"^[a-z0-9_]+$")
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


# MySQL system schemas. These can NEVER be queried, even if somebody lists them
# in the allow-list by mistake.
SYSTEM_DATABASES = frozenset(
    {"mysql", "information_schema", "performance_schema", "sys"}
)

logger = logging.getLogger("queryclarify.registry")


# ---------------------------------------------------------------------------
# built-in database ALLOW-LIST
#
# Only names returned by ``builtin_databases()`` may reach MySQL. Sources, in
# priority order (first non-empty wins):
#
#   1. QC_BUILTIN_DATABASES            comma-separated names
#   2. data/builtin_databases.txt      one name per line ('#' comments);
#                                      override the path with
#                                      QC_BUILTIN_DATABASES_FILE
#   3. a provider registered by app.queryclarify that reads the router index
#      (the ChromaDB "spiderman_databases" collection, i.e. exactly the set of
#      databases the product indexed)
#
# If no source yields a name the allow-list is EMPTY and built-in databases
# are unavailable (fail closed).
# ---------------------------------------------------------------------------

_BUILTIN_NAME_OK = re.compile(r"^[a-z0-9_]{1,64}$")
_BUILTIN_TTL_SECONDS = float(os.getenv("QC_BUILTIN_CACHE_SECONDS", "300"))
_FAILURE_RETRY_SECONDS = 10.0

_builtin_provider = None
_builtin_cache: tuple[float, frozenset[str]] | None = None
_builtin_lock = threading.Lock()


def set_builtin_provider(provider) -> None:
    """Register a callable returning an iterable of database names."""

    global _builtin_provider
    _builtin_provider = provider
    invalidate_builtin_cache()


def invalidate_builtin_cache() -> None:
    global _builtin_cache

    with _builtin_lock:
        _builtin_cache = None


def clean_builtin_names(names) -> frozenset[str]:
    """Normalise names and drop anything that must never be allow-listed."""

    cleaned = set()

    for raw in names or ():
        name = str(raw).strip().strip("`").lower()

        if (
            _BUILTIN_NAME_OK.match(name)
            and name not in SYSTEM_DATABASES
            and not USER_DB_ID_RE.match(name)
        ):
            cleaned.add(name)

    return frozenset(cleaned)


def _names_from_env() -> frozenset[str]:
    return clean_builtin_names(
        os.getenv("QC_BUILTIN_DATABASES", "").split(",")
    )


def builtin_allowlist_file() -> Path:
    return Path(
        os.getenv(
            "QC_BUILTIN_DATABASES_FILE",
            str(DATA_DIR / "builtin_databases.txt"),
        )
    )


def _names_from_file() -> frozenset[str]:
    path = builtin_allowlist_file()

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return frozenset()

    lines = [line.split("#", 1)[0].strip() for line in text.splitlines()]

    return clean_builtin_names(lines)


def _load_builtin_names() -> frozenset[str]:
    for source in (_names_from_env, _names_from_file):
        names = source()

        if names:
            return names

    if _builtin_provider is not None:
        return clean_builtin_names(_builtin_provider())

    return frozenset()


def builtin_databases() -> frozenset[str]:
    """The built-in databases that may be queried (cached, fails closed)."""

    global _builtin_cache

    now = time.monotonic()

    with _builtin_lock:
        cached = _builtin_cache

        if cached is not None and now < cached[0]:
            return cached[1]

        previous = cached[1] if cached else frozenset()

    try:
        names = _load_builtin_names()
        expires = now + _BUILTIN_TTL_SECONDS

    except Exception:
        # Index unavailable: keep serving the last known list, otherwise
        # nothing. Retry soon instead of hammering the failing source.
        logger.exception("could not load the built-in database allow-list")
        names = previous
        expires = now + _FAILURE_RETRY_SECONDS

    with _builtin_lock:
        _builtin_cache = (expires, names)

    return names


def is_builtin_allowed(name: str) -> bool:
    normalized = (name or "").strip().strip("`").lower()

    return (
        BUILTIN_ENABLED
        and normalized not in SYSTEM_DATABASES
        and not USER_DB_ID_RE.match(normalized)
        and normalized in builtin_databases()
    )


class DatabaseAccessError(Exception):
    """Unknown database, or one the caller does not own.

    The two cases are deliberately indistinguishable to avoid leaking which
    ids exist.
    """


@dataclass
class DatabaseRecord:
    id: str
    display_name: str
    owner_hash: str
    dialect: str
    storage_path: str
    status: str
    source_type: str
    table_count: int
    row_count: int
    size_bytes: int
    created_at: str
    expires_at: str | None
    error: str | None = None

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("owner_hash", None)
        data.pop("storage_path", None)
        data.pop("error", None)
        return data


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def hash_owner(client_id: str | None) -> str:
    """Return the stored owner hash for a client id ('' when absent/invalid)."""

    if not client_id or not CLIENT_ID_RE.match(client_id):
        return ""

    return hashlib.sha256(
        (_OWNER_SALT + client_id).encode("utf-8")
    ).hexdigest()


def new_database_id() -> str:
    return "u_" + secrets.token_hex(6)


def is_user_database_id(value: str) -> bool:
    return bool(USER_DB_ID_RE.match((value or "").strip().lower()))


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    USER_DB_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(REGISTRY_PATH, timeout=10)
    conn.row_factory = sqlite3.Row

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS databases (
            id            TEXT PRIMARY KEY,
            display_name  TEXT NOT NULL,
            owner_hash    TEXT NOT NULL,
            dialect       TEXT NOT NULL DEFAULT 'sqlite',
            storage_path  TEXT NOT NULL,
            status        TEXT NOT NULL,
            source_type   TEXT NOT NULL,
            table_count   INTEGER NOT NULL DEFAULT 0,
            row_count     INTEGER NOT NULL DEFAULT 0,
            size_bytes    INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL,
            expires_at    TEXT,
            error         TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_databases_owner "
        "ON databases(owner_hash)"
    )

    return conn


@contextmanager
def _db():
    """Open the registry, commit on success, and always close."""

    conn = _connect()

    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _row_to_record(row: sqlite3.Row) -> DatabaseRecord:
    return DatabaseRecord(**{key: row[key] for key in row.keys()})


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def register(
    *,
    db_id: str,
    display_name: str,
    owner_hash: str,
    storage_path: str,
    source_type: str,
    table_count: int,
    row_count: int,
    size_bytes: int,
    status: str = "ready",
    ttl_days: float | None = None,
) -> DatabaseRecord:
    created = _now()
    days = TTL_DAYS if ttl_days is None else ttl_days
    expires = _iso(created + timedelta(days=days)) if days > 0 else None

    with _db() as conn:
        conn.execute(
            """
            INSERT INTO databases (
                id, display_name, owner_hash, dialect, storage_path, status,
                source_type, table_count, row_count, size_bytes,
                created_at, expires_at
            ) VALUES (?, ?, ?, 'sqlite', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                db_id,
                display_name,
                owner_hash,
                storage_path,
                status,
                source_type,
                table_count,
                row_count,
                size_bytes,
                _iso(created),
                expires,
            ),
        )

    record = get(db_id)
    assert record is not None
    return record


def get(db_id: str) -> DatabaseRecord | None:
    db_id = (db_id or "").strip().lower()

    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM databases WHERE id = ?", (db_id,)
        ).fetchone()

    return _row_to_record(row) if row else None


def list_for_owner(owner_hash: str) -> list[DatabaseRecord]:
    if not owner_hash:
        return []

    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM databases WHERE owner_hash = ? "
            "ORDER BY created_at DESC",
            (owner_hash,),
        ).fetchall()

    return [
        record
        for record in map(_row_to_record, rows)
        if not is_expired(record)
    ]


def usage_for_owner(owner_hash: str) -> tuple[int, int]:
    """Return ``(database_count, total_bytes)`` for an owner."""

    with _db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS b "
            "FROM databases WHERE owner_hash = ?",
            (owner_hash,),
        ).fetchone()

    return int(row["n"]), int(row["b"])


def delete(db_id: str) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM databases WHERE id = ?", (db_id,))


def expired_records() -> list[DatabaseRecord]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM databases WHERE expires_at IS NOT NULL"
        ).fetchall()

    return [
        record
        for record in map(_row_to_record, rows)
        if is_expired(record)
    ]


def is_expired(record: DatabaseRecord) -> bool:
    if not record.expires_at:
        return False

    return datetime.fromisoformat(record.expires_at) <= _now()


# ---------------------------------------------------------------------------
# access control
# ---------------------------------------------------------------------------

def check_access(db_id: str, owner_hash: str) -> DatabaseRecord | None:
    """
    Authorise use of ``db_id`` by ``owner_hash``.

    Returns the ``DatabaseRecord`` for user databases and ``None`` for
    allow-listed built-in databases. Raises ``DatabaseAccessError`` otherwise.

    A user-database id can NEVER fall through to MySQL: anything shaped like
    a user id must exist in the registry and belong to the caller.
    """

    normalized = (db_id or "").strip().strip("`").lower()

    if not normalized:
        raise DatabaseAccessError("Database not found.")

    if is_user_database_id(normalized):
        record = get(normalized)

        if (
            record is None
            or record.status != "ready"
            or is_expired(record)
            or not owner_hash
            or not hmac.compare_digest(record.owner_hash, owner_hash)
        ):
            raise DatabaseAccessError("Database not found.")

        return record

    # Built-in (MySQL) databases: format check AND allow-list. An arbitrary
    # name such as "mysql" or "information_schema" is never accepted.
    if not BUILTIN_NAME_RE.match(normalized) or not is_builtin_allowed(
        normalized
    ):
        raise DatabaseAccessError("Database not found.")

    return None


def dialect_for(db_id: str) -> str:
    """Return ``'sqlite'`` for user databases, ``'mysql'`` otherwise."""

    return "sqlite" if is_user_database_id(db_id) else "mysql"


def storage_path_for(db_id: str) -> Path:
    if not is_user_database_id(db_id):
        raise ValueError("Not a user database id.")

    return USER_DB_DIR / f"{db_id}.sqlite"
