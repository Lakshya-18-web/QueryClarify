from pathlib import Path

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.queryclarify import get_database_engine, get_database_tables


router = APIRouter(
    prefix="/databases",
    tags=["Database Explorer"]
)


# SpiderMan databases included in the project
SPIDERMAN_DATABASES = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "spiderman"
    / "databases"
)


@router.get("")
def list_databases():

    # Prefer the complete SpiderMan dataset list.
    # This gives the frontend all 157 databases available
    # to QueryClarify's RAG layer.
    if SPIDERMAN_DATABASES.exists():

        databases = sorted(
            folder.name
            for folder in SPIDERMAN_DATABASES.iterdir()
            if folder.is_dir()
        )

        return {
            "count": len(databases),
            "databases": databases,
        }

    # Fallback to databases actually available in TiDB.
    try:

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