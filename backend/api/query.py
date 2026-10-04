import logging

from fastapi import APIRouter, Header, HTTPException

from app.secrets_redaction import redact

from backend.schemas.query import (
    QueryRequest,
    QueryResponse
)

from backend.services.query_service import (
    process_query
)


logger = logging.getLogger("queryclarify.api.query")


router = APIRouter(
    prefix="/query",
    tags=["Query"]
)


@router.post(
    "",
    response_model=QueryResponse
)
def query(
    request: QueryRequest,
    x_client_id: str | None = Header(default=None)
):

    try:

        result = process_query(
            question=request.question,
            clarification=request.clarification,
            database=request.database,
            client_id=x_client_id
        )

        return result

    except Exception as e:

        # Full (redacted) traceback goes to the server log; the client gets the
        # message with any credentials masked.
        logger.exception("query failed")

        raise HTTPException(
            status_code=500,
            detail=redact(e)[:500]
        )