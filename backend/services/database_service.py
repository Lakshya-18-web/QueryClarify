"""
Orchestrates the life-cycle of user-supplied databases:

    upload -> validate/convert -> index schema (RAG) -> register
    delete / expire -> remove index entries -> delete file -> unregister

The vector store is resolved lazily so importing this module never loads the
embedding model.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app import db_engines, db_ingest, db_registry

logger = logging.getLogger("queryclarify.databases")


class QuotaExceeded(Exception):
    """The owner is over their database-count or storage quota."""


def get_store() -> Any:
    """The Chroma collection that holds table-level schema documents."""

    from app import queryclarify

    return queryclarify.vectorstore


def limits() -> dict[str, Any]:
    return {
        "max_upload_mb": db_ingest.MAX_UPLOAD_BYTES // (1024 * 1024),
        "max_files": db_ingest.MAX_FILES,
        "max_tables": db_ingest.MAX_TABLES,
        "max_rows_per_table": db_ingest.MAX_ROWS_PER_TABLE,
        "max_databases_per_user": db_registry.MAX_DATABASES_PER_OWNER,
        "max_storage_mb_per_user": db_registry.MAX_BYTES_PER_OWNER
        // (1024 * 1024),
        "retention_days": db_registry.TTL_DAYS or None,
        "accepted_extensions": sorted(
            db_ingest.SQLITE_EXTENSIONS | db_ingest.TABULAR_EXTENSIONS
        ),
    }


def _display_name(requested: str | None, files: list[Path]) -> str:
    base = requested or (files[0].stem if files else "") or "Untitled database"
    return db_ingest.clean_text(base, 60) or "Untitled database"


def create_user_database(
    owner_hash: str,
    requested_name: str | None,
    files: list[Path],
) -> dict[str, Any]:
    """
    Build, index and register a database for ``owner_hash``.

    Raises ``QuotaExceeded`` or ``db_ingest.IngestError``. Nothing is left
    behind (file, index entries, registry row) if any step fails.
    """

    if not owner_hash:
        raise PermissionError("A valid client id is required.")

    purge_expired()

    count, used = db_registry.usage_for_owner(owner_hash)

    if count >= db_registry.MAX_DATABASES_PER_OWNER:
        raise QuotaExceeded(
            f"You can keep at most {db_registry.MAX_DATABASES_PER_OWNER} "
            "databases. Delete one to upload another."
        )

    db_id = db_registry.new_database_id()
    dest = db_registry.storage_path_for(db_id)
    store = None
    indexed = False

    try:
        report = db_ingest.build_database(files, dest)

        size = dest.stat().st_size

        if used + size > db_registry.MAX_BYTES_PER_OWNER:
            raise QuotaExceeded(
                "This upload would exceed your storage quota of "
                f"{db_registry.MAX_BYTES_PER_OWNER // (1024 * 1024)} MB."
            )

        documents = db_ingest.build_schema_documents(
            db_id, dest, report.tables
        )

        store = get_store()
        db_ingest.index_documents(store, db_id, documents)
        indexed = True

        record = db_registry.register(
            db_id=db_id,
            display_name=_display_name(requested_name, files),
            owner_hash=owner_hash,
            storage_path=str(dest),
            source_type=report.source_type,
            table_count=len(report.tables),
            row_count=report.total_rows,
            size_bytes=size,
        )

    except BaseException:
        _cleanup(db_id, dest, store if indexed else None)
        raise

    logger.info(
        "user database created id=%s tables=%d rows=%d",
        db_id, len(report.tables), report.total_rows,
    )

    return {
        "database": record.public(),
        "tables": [
            {
                "name": t.name,
                "original_name": t.original,
                "columns": len(t.columns),
                "rows": t.row_count,
                "relationships": [
                    {
                        "column": fk.column,
                        "references": f"{fk.ref_table}.{fk.ref_column}",
                        "inferred": fk.inferred,
                    }
                    for fk in t.foreign_keys
                ],
            }
            for t in report.tables
        ],
        "warnings": report.warnings,
    }


def _cleanup(db_id: str, path: Path, store: Any | None) -> None:
    """Best-effort removal of every trace of a database."""

    try:
        db_engines.forget_engine(path)
    except Exception:
        logger.exception("could not dispose engine for %s", db_id)

    if store is not None:
        try:
            db_ingest.remove_from_index(store, db_id)
        except Exception:
            logger.exception("could not remove index entries for %s", db_id)

    for candidate in (
        path,
        path.with_suffix(".scratch"),
        path.with_suffix(".building"),
    ):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            logger.exception("could not delete %s", candidate.name)


def delete_user_database(db_id: str, owner_hash: str) -> None:
    """Delete a database the caller owns (raises DatabaseAccessError)."""

    record = db_registry.check_access(db_id, owner_hash)

    if record is None:
        raise db_registry.DatabaseAccessError("Database not found.")

    _remove(record)


def _remove(record: db_registry.DatabaseRecord) -> None:
    store = None

    try:
        store = get_store()
    except Exception:
        logger.exception("vector store unavailable while deleting")

    _cleanup(record.id, Path(record.storage_path), store)
    db_registry.delete(record.id)


def purge_expired() -> int:
    """Delete every expired database. Returns how many were removed."""

    removed = 0

    for record in db_registry.expired_records():
        try:
            _remove(record)
            removed += 1
        except Exception:
            logger.exception("could not purge %s", record.id)

    return removed


def list_user_databases(owner_hash: str) -> list[dict[str, Any]]:
    return [r.public() for r in db_registry.list_for_owner(owner_hash)]


def list_databases_response(owner_hash: str, engine_for=None) -> dict[str, Any]:
    """
    Body of ``GET /api/databases``: the allow-listed built-in databases plus the
    caller's own uploads.

    The response contains database NAMES and upload metadata only. It never
    contains credentials, connection strings, file paths or owner hashes, and
    driver errors are replaced by a generic message (the details are logged,
    with secrets redacted).

    ``engine_for(name)`` returns a SQLAlchemy engine; it defaults to the
    application's allow-listed MySQL factory and exists so tests can inject one.
    """

    databases: list[str] = []
    builtin_error = None

    if db_registry.BUILTIN_ENABLED:

        # Only allow-listed databases are ever offered. System schemas
        # (mysql, information_schema, ...) can never appear here.
        allowed = sorted(db_registry.builtin_databases())

        if not allowed:
            builtin_error = "No built-in databases are configured."

        else:
            databases = allowed

            try:
                if engine_for is None:
                    from app.queryclarify import get_database_engine as engine_for

                from sqlalchemy import text

                # Narrow the list to what the MySQL account can really see
                # (a SELECT-only account sees only its granted databases).
                with engine_for(allowed[0]).connect() as connection:
                    visible = {
                        str(row[0]).lower()
                        for row in connection.execute(
                            text("SHOW DATABASES")
                        ).fetchall()
                    }

                databases = [name for name in allowed if name in visible] or allowed

            except Exception:
                # Keep the endpoint usable so people can still reach their own
                # uploaded databases if MySQL is down.
                logger.exception("could not verify built-in databases")
                builtin_error = "Built-in databases are temporarily unavailable."

    return {
        "count": len(databases),
        "databases": databases,
        "user_databases": list_user_databases(owner_hash),
        "builtin_enabled": db_registry.BUILTIN_ENABLED,
        "builtin_error": builtin_error,
    }
