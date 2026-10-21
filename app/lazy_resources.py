"""Thread-safe lazy resources and Gemini API embeddings."""

from __future__ import annotations

import os
import threading
from typing import Any, Callable

from langchain_core.embeddings import Embeddings
from langchain_google_genai import GoogleGenerativeAIEmbeddings

_UNSET = object()

EMBEDDING_MODEL_NAME = "gemini-embedding-001"


class LazyResource:
    """Build a resource once, on first use, with thread safety."""

    def __init__(self, factory: Callable[[], Any], name: str = "resource"):
        self._factory = factory
        self._name = name
        self._lock = threading.RLock()
        self._value: Any = _UNSET

    @property
    def initialized(self) -> bool:
        return self._value is not _UNSET

    def get(self) -> Any:
        if self._value is not _UNSET:
            return self._value

        with self._lock:
            if self._value is _UNSET:
                self._value = self._factory()
            return self._value

    def reset(self) -> None:
        with self._lock:
            self._value = _UNSET


class EmbeddingMemoryError(RuntimeError):
    """Retained for compatibility with existing imports."""


class LazyEmbeddings(Embeddings):
    """Lazy wrapper around Gemini's remote embedding API."""

    def __init__(self, loader: Callable[[], Any] | None = None):
        self._resource = LazyResource(
            loader or self._load_gemini_embeddings,
            "Gemini embeddings",
        )

    @staticmethod
    def _load_gemini_embeddings() -> GoogleGenerativeAIEmbeddings:
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY is not configured.")

        return GoogleGenerativeAIEmbeddings(
            model=EMBEDDING_MODEL_NAME,
            google_api_key=api_key,
        )

    @property
    def loaded(self) -> bool:
        return self._resource.initialized

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        model = self._resource.get()
        return model.embed_documents(
            texts,
            task_type="RETRIEVAL_DOCUMENT",
        )

    def embed_query(self, text: str) -> list[float]:
        model = self._resource.get()
        return model.embed_query(
            text,
            task_type="RETRIEVAL_QUERY",
        )

    def unload(self) -> None:
        self._resource.reset()




def embedding_model_loaded() -> bool:
    """Compatibility helper for the remote embedding client."""
    return False

def require_embedding_headroom() -> None:
    """Compatibility no-op: remote embeddings don't load a local model."""
    return None

_embedding_client: LazyEmbeddings | None = None