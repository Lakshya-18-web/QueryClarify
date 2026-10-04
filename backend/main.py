import asyncio
import logging
import os

from app import env_config  # noqa: F401  (loads .env first)
from app.secrets_redaction import install_log_redaction

# Mask credentials in EVERY log record (including tracebacks) before anything
# else can log.
install_log_redaction()
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.query import router as query_router
from backend.api.history import router as history_router
from backend.api.database import router as database_router
from backend.api.schema import router as schema_router


logger = logging.getLogger("queryclarify")

CLEANUP_INTERVAL_SECONDS = 3600


async def _cleanup_loop():
    """Periodically delete expired user databases."""

    from backend.services import database_service

    while True:

        try:
            removed = await asyncio.to_thread(
                database_service.purge_expired
            )

            if removed:
                logger.info("purged %d expired databases", removed)

        except Exception:
            logger.exception("expired-database cleanup failed")

        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


def _log_security_report():
    """Log obvious MySQL / allow-list misconfigurations at start-up."""

    from app import db_registry, mysql_hardening

    for warning in mysql_hardening.security_warnings():
        logger.warning("SECURITY: %s", warning)

    if db_registry.owner_salt_is_default():
        logger.warning(
            "SECURITY: QC_OWNER_SALT is not set; the public default salt is "
            "in use. Set it to a long random value before the first upload."
        )

    # Reads the account's REAL grants: having MYSQL_RO_USER set proves nothing
    # about what that account is allowed to do.
    if db_registry.BUILTIN_ENABLED:
        for finding in mysql_hardening.privilege_findings():
            logger.warning("SECURITY: MySQL account: %s", finding)

    if db_registry.BUILTIN_ENABLED:
        logger.info(
            "built-in database allow-list: %d databases",
            len(db_registry.builtin_databases()),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):

    try:
        await asyncio.to_thread(_log_security_report)
    except Exception:
        logger.exception("security report failed")

    task = asyncio.create_task(_cleanup_loop())

    try:
        yield
    finally:
        task.cancel()


app = FastAPI(
    title="QueryClarify API",
    description="RAG-Based Agentic Text-to-SQL API",
    version="1.1.0",
    lifespan=lifespan
)


# ---------------------------------------------------------
# CORS
# ---------------------------------------------------------

# Comma-separated list, e.g. QC_CORS_ORIGINS=https://app.example.com
_origins = [
    origin.strip()
    for origin in os.getenv(
        "QC_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------
# API ROUTERS
# ---------------------------------------------------------

app.include_router(
    query_router,
    prefix="/api"
)

app.include_router(
    history_router,
    prefix="/api"
)

app.include_router(
    database_router,
    prefix="/api"
)

app.include_router(
    schema_router,
    prefix="/api"
)


# ---------------------------------------------------------
# ROOT
# ---------------------------------------------------------

@app.get("/")
def root():
    return {
        "name": "QueryClarify",
        "status": "running",
        "message": "Agentic RAG-Based Text-to-SQL API"
    }


# ---------------------------------------------------------
# HEALTH
# ---------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "healthy"
    }