import logging

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import inspect

from app import db_registry
from app.fk_reflection import get_foreign_keys_safe
from app.secrets_redaction import redact

from app.queryclarify import (
    get_database_engine,
    normalize_identifier,
)


logger = logging.getLogger("queryclarify.api.schema")


router = APIRouter(
    tags=["Schema Explorer"],
)


@router.get(
    "/databases/{database}/tables/{table}/schema"
)
def get_table_schema(
    database: str,
    table: str,
    x_client_id: str | None = Header(default=None),
):
    """
    Return detailed schema information
    for one table.
    """

    try:
        # -------------------------------------------------
        # NORMALIZE IDENTIFIERS
        # -------------------------------------------------

        database = normalize_identifier(database)
        table = normalize_identifier(table)

        # Only the owner may inspect a user database.
        try:
            db_registry.check_access(
                database,
                db_registry.hash_owner(x_client_id),
            )
        except db_registry.DatabaseAccessError:
            raise HTTPException(
                status_code=404,
                detail="Database not found.",
            )

        # -------------------------------------------------
        # DATABASE ENGINE
        # -------------------------------------------------

        engine = get_database_engine(database)

        inspector = inspect(engine)

        # -------------------------------------------------
        # FIND ACTUAL TABLE NAME
        # -------------------------------------------------

        actual_tables = inspector.get_table_names()

        table_map = {
            normalize_identifier(name): name
            for name in actual_tables
        }

        if table not in table_map:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Table '{table}' was not found "
                    f"in database '{database}'."
                ),
            )

        actual_table = table_map[table]

        # -------------------------------------------------
        # COLUMNS
        # -------------------------------------------------

        columns = inspector.get_columns(
            actual_table
        )

        # -------------------------------------------------
        # PRIMARY KEY
        # -------------------------------------------------

        pk_info = inspector.get_pk_constraint(
            actual_table
        )

        primary_keys = pk_info.get(
            "constrained_columns",
            [],
        )

        # -------------------------------------------------
        # FOREIGN KEYS
        # -------------------------------------------------

        fk_info = get_foreign_keys_safe(engine, inspector, 
            actual_table
        )

        foreign_keys = []

        for fk in fk_info:

            constrained = fk.get(
                "constrained_columns",
                [],
            )

            referred_table = fk.get(
                "referred_table"
            )

            referred_columns = fk.get(
                "referred_columns",
                [],
            )

            for index, column in enumerate(
                constrained
            ):

                referenced_column = (
                    referred_columns[index]
                    if index < len(referred_columns)
                    else None
                )

                foreign_keys.append(
                    {
                        "column": column,
                        "references_table": referred_table,
                        "references_column": referenced_column,
                    }
                )

        # -------------------------------------------------
        # COLUMN RESPONSE
        # -------------------------------------------------

        column_response = []

        for column in columns:

            column_name = column["name"]

            column_type = str(
                column["type"]
            )

            is_primary_key = (
                column_name in primary_keys
            )

            foreign_key = next(
                (
                    fk
                    for fk in foreign_keys
                    if fk["column"] == column_name
                ),
                None,
            )

            column_response.append(
                {
                    "name": column_name,
                    "type": column_type,
                    "nullable": bool(
                        column.get(
                            "nullable",
                            True,
                        )
                    ),
                    "primary_key": is_primary_key,
                    "foreign_key": (
                        foreign_key is not None
                    ),
                    "references_table": (
                        foreign_key["references_table"]
                        if foreign_key
                        else None
                    ),
                    "references_column": (
                        foreign_key["references_column"]
                        if foreign_key
                        else None
                    ),
                }
            )

        # -------------------------------------------------
        # RESPONSE
        # -------------------------------------------------

        return {
            "database": database,
            "table": actual_table,
            "column_count": len(
                column_response
            ),
            "columns": column_response,
            "primary_keys": primary_keys,
            "foreign_keys": foreign_keys,
        }

    except HTTPException:
        raise

    except Exception as error:

        # Full (redacted) traceback to the server log; the client only gets the
        # message with credentials masked.
        logger.exception("could not read table schema")

        raise HTTPException(
            status_code=500,
            detail=redact(error)[:500],
        )