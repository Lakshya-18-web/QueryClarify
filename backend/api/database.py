from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.queryclarify import get_database_engine, get_database_tables


router = APIRouter(
    prefix="/databases",
    tags=["Database Explorer"]
)


@router.get("")
def list_databases():

    try:
        # Use an existing loaded database only to obtain
        # the MySQL connection.
        engine = get_database_engine("college_3")

        with engine.connect() as connection:

            rows = connection.execute(
                text("SHOW DATABASES")
            ).fetchall()

        excluded = {
            "information_schema",
            "mysql",
            "performance_schema",
            "sys",
        }

        databases = sorted(
            [
                str(row[0])
                for row in rows
                if str(row[0]).lower() not in excluded
            ]
        )

        return {
            "count": len(databases),
            "databases": databases,
        }

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=f"Unable to load databases: {str(e)}"
        )


@router.get("/{database}/tables")
def list_tables(database: str):

    try:

        tables = get_database_tables(database)

        return {
            "database": database,
            "count": len(tables),
            "tables": tables,
        }

    except Exception as e:

        raise HTTPException(
            status_code=404,
            detail=f"Unable to load database '{database}': {str(e)}"
        )