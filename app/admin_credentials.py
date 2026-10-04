"""
MySQL credentials for the offline scripts in this repository (data loaders,
repair tools, evaluation and diagnostic utilities).

Nothing here is hard-coded: every value comes from environment variables
(or ``app/.env``, which is git-ignored). Two kinds of tool exist:

ADMIN tools (load_*.py, fix_*.py, repair_*.py) CREATE databases and load data,
so they genuinely need a privileged account. Use ``admin_settings()``:

    MYSQL_ADMIN_USER / MYSQL_ADMIN_PASSWORD      preferred
    MYSQL_USER / MYSQL_PASSWORD                  legacy fallback; a notice is
                                                 printed (never the password)

READ-ONLY tools (check_tables.py, schema_reader.py, evaluate.py, ...) only
read. Use ``read_only_settings()``, which prefers the SELECT-only
``MYSQL_RO_USER`` / ``MYSQL_RO_PASSWORD`` account and falls back to the legacy
pair exactly like the application does.

A missing password is an ERROR, never an implicit empty password.
"""

from __future__ import annotations

try:
    from app import env_config  # noqa: F401  (loads .env first)
except ImportError:  # executed as a plain script from inside app/
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from app import env_config  # noqa: F401

import os
import sys
from dataclasses import dataclass, field

from app import mysql_hardening


class MissingCredentialsError(RuntimeError):
    """Required MySQL credentials are not configured."""


@dataclass(frozen=True)
class MySQLSettings:
    host: str
    port: int
    user: str
    # repr=False: the password can never show up in a repr, traceback or log.
    password: str = field(repr=False)
    source: str = ""

    @property
    def administrative(self) -> bool:
        return mysql_hardening.is_administrative_account(self.user)


_notice_printed = set()


def _notice(message: str) -> None:
    if message not in _notice_printed:
        _notice_printed.add(message)
        print(f"NOTICE: {message}", file=sys.stderr)


def _host_port() -> tuple[str, int]:
    host = os.getenv("MYSQL_HOST", "localhost").strip() or "localhost"

    try:
        port = int(os.getenv("MYSQL_PORT", "3306"))
    except ValueError as exc:
        raise MissingCredentialsError("MYSQL_PORT must be a number.") from exc

    return host, port


def admin_settings() -> MySQLSettings:
    """Privileged credentials for tools that create databases / load data."""

    host, port = _host_port()

    user = os.getenv("MYSQL_ADMIN_USER", "").strip()
    password = os.getenv("MYSQL_ADMIN_PASSWORD", "")

    if user and password:
        return MySQLSettings(host, port, user, password, "MYSQL_ADMIN_*")

    # Legacy fallback (documented): MYSQL_USER / MYSQL_PASSWORD, where the user
    # name historically defaulted to "root".
    legacy_user = os.getenv("MYSQL_USER", "").strip() or "root"
    legacy_password = os.getenv("MYSQL_PASSWORD", "")

    if legacy_password:
        _notice(
            "this admin tool is using the legacy MYSQL_USER/MYSQL_PASSWORD "
            f"credentials (user '{legacy_user}'). Set MYSQL_ADMIN_USER and "
            "MYSQL_ADMIN_PASSWORD to choose the account explicitly."
        )
        return MySQLSettings(host, port, legacy_user, legacy_password, "legacy")

    raise MissingCredentialsError(
        "No MySQL admin credentials configured. Set MYSQL_ADMIN_USER and "
        "MYSQL_ADMIN_PASSWORD in app/.env (or the environment). "
        "Empty passwords are not accepted."
    )


def read_only_settings() -> MySQLSettings:
    """Credentials for read-only tools (prefers the SELECT-only account)."""

    host, port = _host_port()
    user, password, read_only_account = mysql_hardening.mysql_credentials()

    if not password:
        raise MissingCredentialsError(
            "No MySQL credentials configured. Set MYSQL_RO_USER and "
            "MYSQL_RO_PASSWORD (recommended) or MYSQL_PASSWORD in app/.env."
        )

    source = "MYSQL_RO_*" if read_only_account else "legacy"

    if not read_only_account:
        _notice(
            "this tool is using the legacy MYSQL_USER/MYSQL_PASSWORD "
            f"credentials (user '{user}'). Prefer MYSQL_RO_USER / "
            "MYSQL_RO_PASSWORD for read-only tools."
        )

    return MySQLSettings(host, port, user, password, source)


def mysql_url(settings: MySQLSettings, database: str | None = None):
    """SQLAlchemy URL (password is masked in str()/repr() by SQLAlchemy)."""

    return mysql_hardening.build_mysql_url(
        settings.user, settings.password, settings.host, settings.port, database
    )


def pymysql_kwargs(settings: MySQLSettings, database: str | None = None, **extra):
    """Keyword arguments for ``pymysql.connect``."""

    kwargs = {
        "host": settings.host,
        "port": settings.port,
        "user": settings.user,
        "password": settings.password,
    }

    if database:
        kwargs["database"] = database

    kwargs.update(extra)

    return kwargs
