import logging
import re
import tempfile
from pathlib import Path

from fastapi import (
    APIRouter,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
)
from app import db_ingest, db_registry
from app.secrets_redaction import redact
from app.queryclarify import get_database_engine, get_database_tables
from backend.services import database_service


logger = logging.getLogger("queryclarify.api.databases")


router = APIRouter(
    prefix="/databases",
    tags=["Database Explorer"]
)


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def authorize_database(database: str, client_id: str | None) -> str:
    """
    Return the normalised database name if the caller may use it.

    Unknown databases and databases owned by someone else both yield the
    same 404 so ids cannot be probed.
    """

    try:
        db_registry.check_access(
            database,
            db_registry.hash_owner(client_id)
        )
    except db_registry.DatabaseAccessError:
        raise HTTPException(
            status_code=404,
            detail="Database not found."
        )

    return database.strip().strip("`").lower()


def _save_uploads(files: list[UploadFile], folder: Path) -> list[Path]:
    """Stream uploads to disk, enforcing the size limit while reading."""

    if not files:
        raise HTTPException(
            status_code=422,
            detail="No file was uploaded."
        )

    if len(files) > db_ingest.MAX_FILES:
        raise HTTPException(
            status_code=422,
            detail=f"Too many files (maximum {db_ingest.MAX_FILES})."
        )

    saved: list[Path] = []
    total = 0
    used_names: set[str] = set()

    for index, upload in enumerate(files):

        name = Path(upload.filename or f"upload_{index}").name
        name = re.sub(r"[^A-Za-z0-9._ -]", "_", name) or f"upload_{index}"

        if name.lower() in used_names:
            name = f"{index}_{name}"

        used_names.add(name.lower())

        target = folder / name

        with open(target, "wb") as handle:

            while True:

                chunk = upload.file.read(1024 * 1024)

                if not chunk:
                    break

                total += len(chunk)

                if total > db_ingest.MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "Upload is too large (maximum "
                            f"{db_ingest.MAX_UPLOAD_BYTES // (1024 * 1024)}"
                            " MB)."
                        )
                    )

                handle.write(chunk)

        saved.append(target)

    return saved


# ---------------------------------------------------------
# List databases (built-in + the caller's own)
# ---------------------------------------------------------

@router.get("")
def list_databases(
    x_client_id: str | None = Header(default=None)
):

    return database_service.list_databases_response(
        db_registry.hash_owner(x_client_id),
        engine_for=get_database_engine,
    )


# ---------------------------------------------------------
# Upload limits (drives the upload UI)
# ---------------------------------------------------------

@router.get("/limits")
def upload_limits():

    return database_service.limits()


# ---------------------------------------------------------
# Upload a database
# ---------------------------------------------------------

@router.post("/upload", status_code=201)
def upload_database(
    files: list[UploadFile] = File(...),
    name: str | None = Form(default=None),
    x_client_id: str | None = Header(default=None),
):

    owner = db_registry.hash_owner(x_client_id)

    if not owner:
        raise HTTPException(
            status_code=400,
            detail="Missing or invalid X-Client-Id header."
        )

    with tempfile.TemporaryDirectory(prefix="qc_upload_") as folder:

        paths = _save_uploads(files, Path(folder))

        try:

            return database_service.create_user_database(
                owner,
                name,
                paths
            )

        except db_ingest.IngestError as e:
            raise HTTPException(status_code=422, detail=redact(e))

        except database_service.QuotaExceeded as e:
            raise HTTPException(status_code=429, detail=redact(e))

        except PermissionError as e:
            raise HTTPException(status_code=400, detail=redact(e))

        except Exception:
            # Details are logged by the service; do not leak internals.
            raise HTTPException(
                status_code=500,
                detail="The database could not be processed."
            )


# ---------------------------------------------------------
# Delete one of the caller's databases
# ---------------------------------------------------------

@router.delete("/{database}")
def delete_database(
    database: str,
    x_client_id: str | None = Header(default=None),
):

    try:

        database_service.delete_user_database(
            database,
            db_registry.hash_owner(x_client_id)
        )

    except db_registry.DatabaseAccessError:
        raise HTTPException(
            status_code=404,
            detail="Database not found."
        )

    return {
        "message": "Database deleted.",
        "database": database,
    }


# ---------------------------------------------------------
# Tables
# ---------------------------------------------------------

@router.get("/{database}/tables")
def list_tables(
    database: str,
    x_client_id: str | None = Header(default=None),
):

    database = authorize_database(database, x_client_id)

    try:

        tables = get_database_tables(database)

        return {
            "database": database,
            "count": len(tables),
            "tables": tables,
        }

    except Exception:

        logger.exception("could not load tables for %s", database)

        raise HTTPException(
            status_code=404,
            detail="Unable to load this database."
        )
