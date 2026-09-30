from typing import Any, Optional

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=2000
    )

    clarification: Optional[str] = None

    database: Optional[str] = None


class QueryResponse(BaseModel):
    status: str

    question: str

    database: Optional[str] = None

    clarification_required: bool = False

    clarification_type: Optional[str] = None

    clarification_question: Optional[str] = None

    database_candidates: list[str] = []

    sql: Optional[str] = None

    result: Optional[list[dict[str, Any]]] = None

    retrieved_tables: list[str] = []

    validation_valid: bool = False

    validation_message: Optional[str] = None

    execution_error: Optional[str] = None

    retries: int = 0

    message: Optional[str] = None