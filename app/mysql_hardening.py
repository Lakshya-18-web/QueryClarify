"""
Hardening for the built-in (Spider) MySQL databases.

Defence in depth, from the outside in:

1. ALLOW-LIST      Only databases listed by ``db_registry.builtin_databases()``
                   can be reached. System schemas can never be reached, even if
                   someone adds them to the allow-list. Enforced in
                   ``get_mysql_engine`` so *every* code path is covered.
2. LEAST PRIVILEGE ``MYSQL_RO_USER`` / ``MYSQL_RO_PASSWORD`` select a
                   SELECT-only account. ``build_grant_sql`` generates its grants
                   for exactly the allow-listed databases (no FILE privilege,
                   no access to ``mysql.*``).
3. SESSION LIMITS  Every new connection is set to READ ONLY and given a
                   server-side statement timeout (MySQL ``MAX_EXECUTION_TIME``
                   or MariaDB ``max_statement_time``).
4. CLIENT TIMEOUTS Connect/read/write socket timeouts so a stuck server cannot
                   pin a worker forever.
5. SQL GUARDRAIL   ``app/guardrails.py`` rejects dangerous constructs before the
                   SQL ever reaches MySQL.

Credentials are read from the environment only and are never logged, returned
or embedded in generated SQL (see ``app/secrets_redaction.py``).

Standard library only at import time; SQLAlchemy is imported lazily.
"""

from __future__ import annotations

from app import env_config  # noqa: F401  (loads .env first)
from app.guardrails import MAX_RESULT_ROWS
from app.secrets_redaction import contains_secret, secret_values

import logging
import os
import re
import threading
from typing import Any

logger = logging.getLogger("queryclarify.mysql")

DB_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
MYSQL_USER_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
MYSQL_HOST_RE = re.compile(r"^[A-Za-z0-9_.%:\-]{1,255}$")


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default

    return value if value > 0 else default


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------

def mysql_credentials() -> tuple[str, str, bool]:
    """
    Return ``(user, password, is_read_only_account)``.

    ``MYSQL_RO_USER`` (+ ``MYSQL_RO_PASSWORD``) wins when set. That is the
    intended, SELECT-only account.

    LEGACY FALLBACK, used ONLY when ``MYSQL_RO_USER`` is empty: the account named
    by ``MYSQL_USER`` (which has always defaulted to ``root`` when unset) with
    ``MYSQL_PASSWORD``. It exists so older setups keep working. It is an
    administrative account, so ``security_warnings()`` reports it at every
    start-up, and ``QC_REQUIRE_READONLY_MYSQL=true`` refuses it outright.
    """

    ro_user = os.getenv("MYSQL_RO_USER", "").strip()

    if ro_user:
        return ro_user, os.getenv("MYSQL_RO_PASSWORD", ""), True

    # noqa: secret  (the legacy default is an account NAME, not a credential;
    # the password always comes from the environment)
    legacy_user = os.getenv("MYSQL_USER", "root")  # noqa: secret

    return (
        legacy_user.strip() or "root",  # noqa: secret
        os.getenv("MYSQL_PASSWORD") or "",
        False,
    )


def credentials_configured() -> bool:
    """True when some MySQL password/account has been configured."""

    if os.getenv("MYSQL_RO_USER", "").strip():
        return bool(os.getenv("MYSQL_RO_PASSWORD"))

    return bool(os.getenv("MYSQL_PASSWORD"))


# Account names that are administrative by convention. The application must
# never serve user questions through one of these.
ADMIN_ACCOUNT_NAMES = frozenset({"root", "admin", "administrator", "dba", "sa"})


def is_administrative_account(user: str) -> bool:
    return (user or "").strip().lower() in ADMIN_ACCOUNT_NAMES


def require_read_only_account() -> bool:
    """
    Opt-in strict mode (``QC_REQUIRE_READONLY_MYSQL=true``): refuse to serve
    built-in databases unless a dedicated ``MYSQL_RO_USER`` account is used.
    Off by default so existing setups keep working; recommended in production.
    """

    return os.getenv("QC_REQUIRE_READONLY_MYSQL", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def build_mysql_url(
    user: str,
    password: str,
    host: str,
    port: int | str,
    database: str | None = None,
):
    """
    SQLAlchemy URL built from parts (no string formatting, no URL parsing), so
    an odd host/user/password can never make SQLAlchemy echo the credentials in
    an "could not parse URL" error. ``str(url)`` masks the password.
    """

    from sqlalchemy.engine import URL

    return URL.create(
        "mysql+pymysql",
        username=user,
        password=password,
        host=host,
        port=int(port),
        database=database,
    )


# ---------------------------------------------------------------------------
# connection / session settings
# ---------------------------------------------------------------------------

def connect_args() -> dict[str, int]:
    """pymysql socket timeouts (seconds)."""

    return {
        "connect_timeout": _env_int("QC_MYSQL_CONNECT_TIMEOUT", 5),
        "read_timeout": _env_int("QC_MYSQL_READ_TIMEOUT", 30),
        "write_timeout": _env_int("QC_MYSQL_WRITE_TIMEOUT", 30),
    }


def max_execution_ms() -> int:
    return _env_int("QC_MYSQL_MAX_EXECUTION_MS", 10_000)


def max_select_rows() -> int:
    """
    Server-side row cap for SELECTs that have no LIMIT of their own
    (``sql_select_limit``). One more than the application cap so the client
    can still tell that a result was truncated.
    """

    return _env_int("QC_MYSQL_MAX_SELECT_ROWS", MAX_RESULT_ROWS + 1)


def apply_session_limits(dbapi_connection: Any) -> list[str]:
    """
    Make a freshly opened MySQL/MariaDB session safe. Returns the labels that
    were applied. Failures are logged, never raised: an unsupported variable
    (for example MAX_EXECUTION_TIME on MariaDB) must not break the connection.
    """

    applied: list[str] = []
    cursor = dbapi_connection.cursor()

    def attempt(label: str, statements: list[str]) -> None:
        for statement in statements:
            try:
                cursor.execute(statement)
                applied.append(label)
                return
            except Exception as exc:  # noqa: BLE001
                logger.debug("%s not applied via %r: %r", label, statement, exc)

        logger.warning(
            "Could not apply MySQL session setting '%s'; "
            "relying on the other safeguards.",
            label,
        )

    try:
        attempt("read_only", ["SET SESSION TRANSACTION READ ONLY"])

        ms = max_execution_ms()
        attempt(
            "statement_timeout",
            [
                # MySQL 5.7.8+ (milliseconds)
                f"SET SESSION MAX_EXECUTION_TIME = {ms}",
                # MariaDB 10.1+ (seconds)
                f"SET SESSION max_statement_time = {max(1, ms // 1000)}",
            ],
        )

        # Rows MySQL may return for a SELECT without its own LIMIT. This stops
        # an accidental row explosion (for example a cross join) from being
        # shipped to, and buffered by, the driver. An explicit LIMIT in the SQL
        # takes precedence over this setting, so it is a mitigation, not a
        # guarantee.
        attempt("row_cap", [f"SET SESSION sql_select_limit = {max_select_rows()}"])
    finally:
        try:
            cursor.close()
        except Exception:  # noqa: BLE001
            pass

    return applied


# ---------------------------------------------------------------------------
# engine factory (allow-listed)
# ---------------------------------------------------------------------------

_engines: dict[str, Any] = {}
_engines_lock = threading.Lock()


def get_mysql_engine(database: str):
    """
    SQLAlchemy engine for a built-in database.

    Raises ``ValueError`` for anything that is not on the allow-list (system
    schemas, arbitrary names, unknown databases). Because engines are created
    and cached *only* for allow-listed names, an attacker can no longer grow
    the engine cache with arbitrary names either.
    """

    from app import db_registry

    name = (database or "").strip().strip("`").lower()

    if not db_registry.is_builtin_allowed(name):
        # Deliberately generic: do not reveal whether the name exists.
        raise ValueError("Database is not available.")

    with _engines_lock:
        engine = _engines.get(name)

        if engine is None:
            engine = _create_engine(name)
            _engines[name] = engine

    return engine


def _create_engine(name: str):
    from sqlalchemy import create_engine, event

    user, password, read_only_account = mysql_credentials()

    if require_read_only_account() and (
        not read_only_account or is_administrative_account(user)
    ):
        # No credentials or account names in the message.
        raise RuntimeError(
            "QC_REQUIRE_READONLY_MYSQL is enabled but no dedicated read-only "
            "MySQL account (MYSQL_RO_USER) is configured."
        )

    host = os.getenv("MYSQL_HOST", "localhost")
    port = os.getenv("MYSQL_PORT", "3306")

    engine = create_engine(
        build_mysql_url(user, password, host, port, name),
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args=connect_args(),
    )

    @event.listens_for(engine, "connect")
    def _harden_session(dbapi_connection, connection_record):  # noqa: ANN001
        apply_session_limits(dbapi_connection)

    return engine


def forget_mysql_engines() -> None:
    """Dispose and forget every cached engine (tests / allow-list changes)."""

    with _engines_lock:
        engines = list(_engines.values())
        _engines.clear()

    for engine in engines:
        try:
            engine.dispose()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# start-up diagnostics
# ---------------------------------------------------------------------------

def security_warnings() -> list[str]:
    """
    Human-readable problems with the current built-in MySQL setup (offline: no
    connection is made). Messages never contain passwords.

    Which credentials are used:
      * MYSQL_RO_USER set      -> that account (preferred, intended SELECT-only)
      * otherwise              -> the legacy MYSQL_USER (default "root") with
                                  MYSQL_PASSWORD. This fallback exists only so
                                  old setups keep working.
    """

    from app import db_registry

    if not db_registry.BUILTIN_ENABLED:
        return []

    warnings: list[str] = []
    user, _password, read_only_account = mysql_credentials()
    administrative = is_administrative_account(user)

    if read_only_account:
        if administrative:
            warnings.append(
                f"MYSQL_RO_USER is set to '{user}', which is an administrative "
                "account name. It is not a read-only account; create a "
                "SELECT-only user (`python -m app.hardening_tools mysql-grants`)."
            )
    elif administrative:
        warnings.append(
            f"Built-in databases are queried as the ADMINISTRATIVE MySQL account "
            f"'{user}' (legacy MYSQL_USER/MYSQL_PASSWORD fallback). Create a "
            "SELECT-only account with `python -m app.hardening_tools "
            "mysql-grants` and set MYSQL_RO_USER / MYSQL_RO_PASSWORD."
        )
    else:
        warnings.append(
            f"Built-in databases are queried via the legacy MYSQL_USER "
            f"fallback ('{user}'). Confirm it is SELECT-only, or set "
            "MYSQL_RO_USER / MYSQL_RO_PASSWORD."
        )

    if require_read_only_account() and (not read_only_account or administrative):
        warnings.append(
            "QC_REQUIRE_READONLY_MYSQL is enabled: built-in queries are REFUSED "
            "until a dedicated MYSQL_RO_USER account is configured."
        )

    if not db_registry.builtin_databases():
        warnings.append(
            "The built-in database allow-list is empty, so no built-in "
            "database can be queried. Run `python -m app.hardening_tools "
            "export-allowlist` or set QC_BUILTIN_DATABASES."
        )

    return warnings


# ---------------------------------------------------------------------------
# privilege audit (SHOW GRANTS)
#
# Having MYSQL_RO_USER set proves nothing about what that account may do. This
# reads the account's real grants and reports anything beyond SELECT.
# ---------------------------------------------------------------------------

_GRANT_LINE = re.compile(
    r"^\s*GRANT\s+(?P<privs>.+?)\s+ON\s+(?P<object>.+?)\s+TO\s",
    re.IGNORECASE | re.DOTALL,
)
_ROLE_GRANT = re.compile(r"^\s*GRANT\s+[^\s].*\s+TO\s", re.IGNORECASE | re.DOTALL)
_PARENTHESISED = re.compile(r"\([^)]*\)")
_SAFE_PRIVILEGES = frozenset({"SELECT", "USAGE"})


def _scope_database(obj: str) -> tuple[str, bool]:
    """Return ``(database_name, is_global)`` for a GRANT object like `db`.*"""

    cleaned = obj.strip()

    if cleaned in ("*.*", "*"):
        return "", True

    database = cleaned.split(".", 1)[0].strip().strip("`'\"")

    # GRANT patterns escape wildcards: `college\_3`
    return database.replace("\\_", "_").replace("\\%", "%").lower(), False


def audit_grants(grant_lines, allowed_databases=None) -> list[str]:
    """
    Findings for the rows returned by ``SHOW GRANTS``. An empty list means the
    grants look SELECT-only. Raw grant lines are never copied into findings
    (older MySQL versions append a password hash to them).
    """

    from app import db_registry

    allowed = frozenset(allowed_databases or ())
    findings: list[str] = []
    outside: set[str] = set()

    def add(message: str) -> None:
        if message not in findings:
            findings.append(message)

    for line in grant_lines:
        text = str(line)
        match = _GRANT_LINE.match(text)

        if not match:
            if _ROLE_GRANT.match(text):
                add(
                    "The account has role grants that cannot be verified here; "
                    "check them manually."
                )
            continue

        privileges = {
            part.strip().upper()
            for part in _PARENTHESISED.sub("", match.group("privs")).split(",")
            if part.strip()
        }
        database, is_global = _scope_database(match.group("object"))
        scope = "all databases (*.*)" if is_global else f"database `{database}`"

        excess = sorted(privileges - _SAFE_PRIVILEGES)

        if excess:
            add(f"Excess privileges on {scope}: {', '.join(excess)}.")

        if re.search(r"\bWITH\s+GRANT\s+OPTION\b", text, re.IGNORECASE):
            add(f"GRANT OPTION is held on {scope}.")

        if "SELECT" in privileges:
            if is_global:
                add("SELECT is granted on ALL databases (*.*), including the system schemas.")
            elif database in db_registry.SYSTEM_DATABASES:
                add(f"SELECT is granted on the system schema `{database}`.")
            elif allowed and database not in allowed:
                outside.add(database)

    if outside:
        names = sorted(outside)
        shown = ", ".join(names[:5]) + (f" (+{len(names) - 5} more)" if len(names) > 5 else "")
        add(f"SELECT is granted on databases outside the allow-list: {shown}.")

    return findings


def privilege_findings(engine=None) -> list[str]:
    """
    Connect and audit the privileges of the account the application uses.
    Returns findings; an unverifiable account is itself a finding.
    """

    from app import db_registry

    if not db_registry.BUILTIN_ENABLED:
        return []

    allowed = db_registry.builtin_databases()

    try:
        if engine is None:
            if not allowed:
                return []

            engine = get_mysql_engine(sorted(allowed)[0])

        from sqlalchemy import text

        with engine.connect() as connection:
            lines = [
                str(row[0])
                for row in connection.execute(
                    text("SHOW GRANTS FOR CURRENT_USER()")
                ).fetchall()
            ]

    except Exception:  # noqa: BLE001
        logger.warning("could not read the MySQL account's grants")
        return [
            "The MySQL account's privileges could not be verified (SHOW GRANTS "
            "failed). Treat it as NOT least-privilege until checked."
        ]

    return audit_grants(lines, allowed)


# ---------------------------------------------------------------------------
# least-privilege account generator
# ---------------------------------------------------------------------------

def build_grant_sql(
    names: list[str] | set[str] | frozenset[str],
    user: str = "qc_readonly",
    host: str = "%",
    password_placeholder: str = "CHANGE_ME",
) -> str:
    """
    SQL that creates a SELECT-only account for exactly ``names``.

    * The account is created LOCKED with a throw-away placeholder password, so
      forgetting to set a real one cannot leave a usable known-password account.
    * No real password is ever written: it is set interactively afterwards.
    * Every value is validated against a strict pattern, so the output cannot be
      used for SQL injection even if the names came from an untrusted source.
    * Underscores in database names are escaped (``\\_``): in a GRANT, ``_`` is
      a one-character wildcard, and an unescaped name would also match others.
    """

    if not MYSQL_USER_RE.match(user):
        raise ValueError("Invalid MySQL user name.")

    if not MYSQL_HOST_RE.match(host):
        raise ValueError("Invalid MySQL host pattern.")

    if not re.fullmatch(r"[A-Za-z0-9_@#%^*!.\-]{1,64}", password_placeholder):
        raise ValueError("Invalid password placeholder.")

    if password_placeholder in secret_values():
        raise ValueError(
            "Refusing to write a configured secret into generated SQL."
        )

    from app import db_registry

    valid = sorted(
        {
            n
            for n in names
            if DB_NAME_RE.match(n) and n not in db_registry.SYSTEM_DATABASES
        }
    )
    reserved = sorted(
        {n for n in names if n in db_registry.SYSTEM_DATABASES}
    )
    invalid_count = len(
        {n for n in names if n not in valid and n not in reserved}
    )

    account = f"'{user}'@'{host}'"

    lines = [
        "-- Least-privilege account for QueryClarify's built-in databases (SELECT only).",
        "-- Run as a MySQL administrator.",
        "--",
        "-- The account is created LOCKED with a placeholder password. Set the real",
        "-- password INTERACTIVELY in the mysql client; never save it in this file or",
        "-- in version control:",
        f"--   ALTER USER {account} IDENTIFIED BY '<type it here>' ACCOUNT UNLOCK;",
        f"-- Then store MYSQL_RO_USER={user} and MYSQL_RO_PASSWORD in your secret store.",
        "-- (Servers older than MySQL 5.7.6 / MariaDB 10.4.2: remove 'ACCOUNT LOCK'.)",
        f"CREATE USER IF NOT EXISTS {account} "
        f"IDENTIFIED BY '{password_placeholder}' ACCOUNT LOCK;",
        # Start from nothing so re-running the script also removes stale access.
        f"REVOKE ALL PRIVILEGES, GRANT OPTION FROM {account};",
    ]

    for name in valid:
        escaped = name.replace("_", "\\_")
        lines.append(f"GRANT SELECT ON `{escaped}`.* TO {account};")

    # Never echo untrusted text into the generated SQL: report fixed system
    # schema names and a count only.
    for name in reserved:
        lines.append(f"-- skipped reserved system schema: {name}")

    if invalid_count:
        lines.append(
            f"-- skipped {invalid_count} name(s) that are not valid "
            "database names"
        )

    lines.append("FLUSH PRIVILEGES;")

    output = "\n".join(lines) + "\n"

    if contains_secret(output):
        raise ValueError("Generated SQL would contain a configured secret.")

    return output
