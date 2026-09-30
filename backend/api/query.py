from fastapi import APIRouter, HTTPException

from backend.schemas.query import (
    QueryRequest,
    QueryResponse
)

from backend.services.query_service import (
    process_query
)


router = APIRouter(
    prefix="/query",
    tags=["Query"]
)


@router.post(
    "",
    response_model=QueryResponse
)
def query(
    request: QueryRequest
):

    try:

        result = process_query(
            question=request.question,
            clarification=request.clarification,
            database=request.database
        )

        return result

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )