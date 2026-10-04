from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.db_registry import hash_owner


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

# History is private to the client that created it (X-Client-Id header).
# Entries carry an internal "_owner" hash that is never sent to clients.
MAX_HISTORY_PER_OWNER = 200


def _visible(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if not k.startswith("_")}


def _owned(owner: str) -> list[dict[str, Any]]:
    return [e for e in query_history if e["_owner"] == owner]

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
    x_client_id: str | None = Header(default=None),
):
    global _next_history_id

    owner = hash_owner(x_client_id)

    history_entry = {
        "id": _next_history_id,

        "_owner": owner,

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

    # Bound memory: drop this owner's oldest entries beyond the cap.
    mine = _owned(owner)

    for stale in mine[MAX_HISTORY_PER_OWNER:]:
        query_history.remove(stale)

    return {
        "message": "Query added to history.",
        "item": _visible(history_entry),
    }


# ============================================================
# GET ALL HISTORY
# ============================================================

@router.get("")
def get_history(
    x_client_id: str | None = Header(default=None),
):

    mine = [
        _visible(e) for e in _owned(hash_owner(x_client_id))
    ]

    return {
        "count": len(mine),
        "history": mine,
    }


# ============================================================
# GET ONE HISTORY ITEM
# ============================================================

@router.get("/{history_id}")
def get_history_item(
    history_id: int,
    x_client_id: str | None = Header(default=None),
):

    for item in _owned(hash_owner(x_client_id)):

        if item["id"] == history_id:

            return _visible(item)

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
    x_client_id: str | None = Header(default=None),
):

    owner = hash_owner(x_client_id)

    for index, item in enumerate(
        query_history
    ):

        if item["id"] == history_id and item["_owner"] == owner:

            deleted = query_history.pop(index)

            return {
                "message": "History item deleted.",
                "item": _visible(deleted),
            }

    raise HTTPException(
        status_code=404,
        detail="History item not found.",
    )


# ============================================================
# CLEAR ALL HISTORY
# ============================================================

@router.delete("")
def clear_history(
    x_client_id: str | None = Header(default=None),
):

    owner = hash_owner(x_client_id)

    query_history[:] = [
        e for e in query_history if e["_owner"] != owner
    ]

    return {
        "message": "Query history cleared.",
    }