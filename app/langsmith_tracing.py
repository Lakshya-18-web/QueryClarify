"""LangSmith observability helpers for QueryClarify."""

from __future__ import annotations

import os
from functools import wraps
from typing import Any, Callable

from dotenv import load_dotenv

load_dotenv()

# LangSmith tracing is opt-in through the environment.
os.environ.setdefault("LANGSMITH_PROJECT", "QueryClarify")

try:
    from langsmith import traceable
except ImportError:  # Allows the application to run without LangSmith installed.
    def traceable(*args, **kwargs):
        if args and callable(args[0]) and len(args) == 1:
            return args[0]

        def decorator(function: Callable):
            return function

        return decorator


def traced_node(name: str, tags: list[str] | None = None):
    """Decorator for LangGraph nodes/functions."""
    return traceable(
        name=name,
        run_type="chain",
        tags=tags or ["queryclarify"],
    )


def traced_tool(name: str, tags: list[str] | None = None):
    return traceable(
        name=name,
        run_type="tool",
        tags=tags or ["queryclarify", "tool"],
    )


def tracing_status() -> dict[str, Any]:
    return {
        "enabled": os.getenv("LANGSMITH_TRACING", "false").lower() == "true",
        "project": os.getenv("LANGSMITH_PROJECT", "QueryClarify"),
        "api_key_configured": bool(os.getenv("LANGSMITH_API_KEY")),
    }
