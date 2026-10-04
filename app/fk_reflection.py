"""
Foreign-key reflection that survives SQLAlchemy/MySQL reflection failures.

Symptom this fixes (seen on MySQL):

    FK inspection failed for Course: 'TABLENAME'

``inspector.get_foreign_keys()`` raised ``KeyError('TABLENAME')`` for every
table that has foreign keys. QueryClarify then saw an *empty* foreign-key
graph, so bridge tables such as ``Enrolled_in`` were never added to the
schema given to the LLM. The model then invented table names (Enrl, Enroll,
Student_Course) and every retry failed validation.

``get_foreign_keys_safe`` first tries the normal SQLAlchemy inspector (this is
what SQLite uploads and healthy MySQL setups use). If that raises, it reads
the same information straight from ``information_schema`` and returns it in
SQLAlchemy's own shape::

    {"name", "constrained_columns", "referred_schema",
     "referred_table", "referred_columns"}
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("queryclarify.fk")

_FK_SQL = """
SELECT CONSTRAINT_NAME,
       COLUMN_NAME,
       REFERENCED_TABLE_NAME,
       REFERENCED_COLUMN_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = DATABASE()
  AND LOWER(TABLE_NAME) = LOWER(:table_name)
  AND REFERENCED_TABLE_NAME IS NOT NULL
ORDER BY CONSTRAINT_NAME, ORDINAL_POSITION
"""


def group_fk_rows(rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    """
    Turn ``(constraint, column, ref_table, ref_column)`` rows (one per column,
    ordered by constraint then position) into one dict per constraint, so
    composite foreign keys stay together.
    """

    grouped: dict[str, dict[str, Any]] = {}

    for row in rows:
        # Positional access on purpose: column-name casing in
        # information_schema differs between MySQL versions/platforms.
        name, column, ref_table, ref_column = row[0], row[1], row[2], row[3]

        entry = grouped.setdefault(
            str(name),
            {
                "name": str(name),
                "constrained_columns": [],
                "referred_schema": None,
                "referred_table": str(ref_table),
                "referred_columns": [],
            },
        )

        entry["constrained_columns"].append(str(column))
        entry["referred_columns"].append(str(ref_column))

    return list(grouped.values())


def get_foreign_keys_safe(engine, inspector, table: str) -> list[dict[str, Any]]:
    """Foreign keys of ``table``; never raises, returns ``[]`` on failure."""

    try:
        return inspector.get_foreign_keys(table)

    except Exception as exc:
        logger.warning(
            "inspector.get_foreign_keys(%s) failed (%r); "
            "falling back to information_schema",
            table,
            exc,
        )

    try:
        from sqlalchemy import text

        with engine.connect() as connection:
            rows = connection.execute(
                text(_FK_SQL),
                {"table_name": table},
            ).fetchall()

        return group_fk_rows(rows)

    except Exception:
        logger.exception("information_schema foreign-key lookup failed")
        return []
