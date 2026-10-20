"""
Lazy, thread-safe construction of the embedding model.

Why this exists
---------------
The sentence-transformers model (and PyTorch behind it) costs several hundred
MB of RAM. It used to be built when ``app.queryclarify`` was imported, i.e. at
web-server start-up, which exceeds the 512 MB limit of a small Render instance
before the first request is served.

Almost nothing in the request path needs it: routing, schema retrieval and SQL
generation for the built-in databases only READ stored vectors from Chroma
(``Chroma.get``), which never embeds anything. The model is needed only to
*create* vectors: indexing an uploaded database (``add_texts``), the semantic
re-ranking for uploaded databases (``similarity_search``) and the router's
rare empty-result fallback.

So the model is now built on the first call that really embeds text, once per
process, under a lock, and importing the application never imports torch.

The model name and its arguments are unchanged, so new vectors are identical to
the ones already stored in Chroma.

Standard library only (``langchain_core`` is used when installed).
"""

from __future__ import annotations

import gc
import logging
import os
import threading
from typing import Any, Callable, Iterable

try:  # a real Embeddings subclass in production, a plain object in offline tests
    from langchain_core.embeddings import Embeddings as _EmbeddingsBase
except ImportError:  # pragma: no cover
    _EmbeddingsBase = object

logger = logging.getLogger("queryclarify.lazy")

# MUST match the model that produced the vectors already stored in Chroma.
EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

# Rough free memory (MB) needed to import torch + transformers and load the
# model. An ESTIMATE, not a measurement: tune with QC_EMBEDDING_MIN_HEADROOM_MB
# (0 disables the check).
DEFAULT_MIN_HEADROOM_MB = 500

_UNSET = object()


# ---------------------------------------------------------------------------
# build-once holder
# ---------------------------------------------------------------------------

class LazyResource:
    """
    Holds a value that is built on first ``get()``.

    * thread-safe: concurrent first calls build the value exactly once;
    * a failing factory is NOT cached, so the next call retries.
    """

    def __init__(self, factory: Callable[[], Any], name: str = "resource"):
        self._factory = factory
        self._name = name
        self._lock = threading.RLock()
        self._value: Any = _UNSET

    @property
    def initialized(self) -> bool:
        return self._value is not _UNSET

    def get(self) -> Any:
        value = self._value

        if value is not _UNSET:                    # fast path, no lock
            return value

        with self._lock:
            if self._value is _UNSET:              # re-check under the lock
                self._value = self._factory()

            return self._value

    def reset(self) -> None:
        """Forget the value (it is rebuilt on the next ``get``)."""

        with self._lock:
            self._value = _UNSET


# ---------------------------------------------------------------------------
# memory guard
# ---------------------------------------------------------------------------

class EmbeddingMemoryError(RuntimeError):
    """The embedding model cannot be loaded because the process is near its memory limit."""


def memory_limit_bytes(
    paths: Iterable[str] = (
        "/sys/fs/cgroup/memory.max",                      # cgroup v2
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",    # cgroup v1
    ),
) -> int | None:
    """The container's memory limit, or ``None`` when there is none (or it is unknown)."""

    for path in paths:
        try:
            with open(path, encoding="ascii") as handle:
                raw = handle.read().strip()
        except OSError:
            continue

        if not raw or raw == "max":
            return None

        try:
            value = int(raw)
        except ValueError:
            continue

        # cgroup v1 reports a huge sentinel when unlimited
        return value if value < (1 << 50) else None

    return None


def process_rss_bytes(status_path: str = "/proc/self/status") -> int | None:
    """Resident memory of this process (Linux), or ``None`` if unavailable."""

    try:
        with open(status_path, encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass

    return None


def _min_headroom_mb() -> int:
    try:
        return int(os.getenv("QC_EMBEDDING_MIN_HEADROOM_MB", str(DEFAULT_MIN_HEADROOM_MB)))
    except ValueError:
        return DEFAULT_MIN_HEADROOM_MB


def check_memory_headroom(
    required_mb: int | None = None,
    limit_bytes: int | None = None,
    rss_bytes: int | None = None,
) -> None:
    """
    Raise ``EmbeddingMemoryError`` if loading the model would not fit.

    Inputs are injectable for tests; by default they come from the container's
    cgroup limit and this process's resident memory. With no known limit (a
    laptop, an unlimited container) or ``required_mb <= 0`` nothing is checked.
    """

    required = _min_headroom_mb() if required_mb is None else required_mb

    if required <= 0:
        return

    limit = memory_limit_bytes() if limit_bytes is None else limit_bytes
    rss = process_rss_bytes() if rss_bytes is None else rss_bytes

    if limit is None or rss is None:
        return

    headroom_mb = (limit - rss) // (1024 * 1024)

    if headroom_mb < required:
        raise EmbeddingMemoryError(
            "This server does not have enough free memory to load the embedding "
            f"model (about {required} MB needed, {max(headroom_mb, 0)} MB free of "
            f"{limit // (1024 * 1024)} MB). Use an instance with at least 1-2 GB "
            "of RAM to index uploaded databases."
        )


# ---------------------------------------------------------------------------
# the embedding model
# ---------------------------------------------------------------------------

_model_loaded = False


def embedding_model_loaded() -> bool:
    return _model_loaded


def require_embedding_headroom() -> None:
    """
    Fail fast (before any heavy work) if the model is not loaded yet and would
    not fit. A no-op once the model is in memory.
    """

    if not _model_loaded:
        check_memory_headroom()


def _load_sentence_transformer_embeddings() -> Any:
    """Build the real model. Imported here so that importing the app never imports torch."""

    global _model_loaded

    check_memory_headroom()

    from langchain_huggingface import HuggingFaceEmbeddings

    logger.info("loading embedding model %s", EMBEDDING_MODEL_NAME)

    model = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_NAME)
    _model_loaded = True

    return model


class LazyEmbeddings(_EmbeddingsBase):
    """
    Drop-in embedding function that loads the real model on first use.

    A single instance is shared by every Chroma store in the process, so the
    model exists at most once. Creating a Chroma store with it costs nothing.
    """

    def __init__(self, loader: Callable[[], Any] | None = None):
        self._resource = LazyResource(
            loader or _load_sentence_transformer_embeddings, "embedding model"
        )

    @property
    def loaded(self) -> bool:
        return self._resource.initialized

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._resource.get().embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._resource.get().embed_query(text)

    def unload(self) -> None:
        """Drop the model (best effort: the OS may not get all the memory back)."""

        global _model_loaded

        self._resource.reset()
        _model_loaded = False
        gc.collect()
