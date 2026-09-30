from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


# ============================================================
# ROUTER
# ============================================================

router = APIRouter(
    prefix="/history",
    tags=["History"],
)


# ============================================================
# IN-MEMORY HISTORY
# ============================================================

query_history: list[dict[str, Any]] = []

_next_history_id = 1


# ============================================================
# REQUEST MODEL
# ============================================================

class HistoryItem(BaseModel):
    question: str

    database: str | None = None

    sql: str | None = None

    result: list[dict[str, Any]] = Field(
        default_factory=list
    )

    row_count: int = 0

    latency_ms: float | None = None

    validation_valid: bool = False

    retrieved_tables: list[str] = Field(
        default_factory=list
    )

    retries: int = 0


# ============================================================
# ADD QUERY TO HISTORY
# ============================================================

@router.post("")
def add_history(
    item: HistoryItem,
):
    global _next_history_id

    history_entry = {
        "id": _next_history_id,

        "question": item.question,

        "database": item.database,

        "sql": item.sql,

        "result": item.result,

        "row_count": item.row_count,

        "latency_ms": item.latency_ms,

        "validation_valid": item.validation_valid,

        "retrieved_tables": item.retrieved_tables,

        "retries": item.retries,

        "timestamp": datetime.now(
            timezone.utc
        ).isoformat(),
    }

    _next_history_id += 1

    # Newest queries appear first
    query_history.insert(
        0,
        history_entry,
    )

    return {
        "message": "Query added to history.",
        "item": history_entry,
    }


# ============================================================
# GET ALL HISTORY
# ============================================================

@router.get("")
def get_history():

    return {
        "count": len(query_history),
        "history": query_history,
    }


# ============================================================
# GET ONE HISTORY ITEM
# ============================================================

@router.get("/{history_id}")
def get_history_item(
    history_id: int,
):

    for item in query_history:

        if item["id"] == history_id:

            return item

    raise HTTPException(
        status_code=404,
        detail="History item not found.",
    )


# ============================================================
# DELETE ONE HISTORY ITEM
# ============================================================

@router.delete("/{history_id}")
def delete_history_item(
    history_id: int,
):

    for index, item in enumerate(
        query_history
    ):

        if item["id"] == history_id:

            deleted = query_history.pop(index)

            return {
                "message": "History item deleted.",
                "item": deleted,
            }

    raise HTTPException(
        status_code=404,
        detail="History item not found.",
    )


# ============================================================
# CLEAR ALL HISTORY
# ============================================================

@router.delete("")
def clear_history():

    query_history.clear()

    return {
        "message": "Query history cleared.",
    }