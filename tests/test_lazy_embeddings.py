"""
Lazy embedding-model / vector-store initialization.

Goal: importing the web application must not load the embedding model (several
hundred MB of RAM); the model is loaded once, on the first call that really
embeds text, and everything else keeps working.

The real ``app.queryclarify`` is imported here with its heavy third-party
dependencies (Chroma, Hugging Face, SQLAlchemy, LangGraph, Gemini) replaced by
small fakes, so these tests exercise the real wiring without torch, Chroma or
a network.   python -m unittest discover -s tests -v
"""

import ast
import contextlib
import importlib
import io
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="qc_lazy_")
os.environ.setdefault("QC_DATA_DIR", _TMP)

try:
    from helpers import FAKE_API_KEY, FAKE_PASSWORD, env
except ImportError:  # imported as tests.test_lazy_embeddings
    from tests.helpers import FAKE_API_KEY, FAKE_PASSWORD, env

from app import db_registry, lazy_resources  # noqa: E402
from backend.services import database_service  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


# ===========================================================================
# fakes for the heavy third-party libraries
# ===========================================================================

class Fakes:
    """Counters and 'persisted' storage shared by every stubbed library."""

    def __init__(self):
        self.lock = threading.Lock()
        self.model_constructed = 0       # real model loads (the expensive thing)
        self.model_import_accessed = 0   # `from langchain_huggingface import ...`
        self.model_names = []
        self.texts_encoded = 0
        self.chroma_created = []         # collection names, one entry per Chroma(...)
        self.persist_dirs = set()
        self.stores = {}                 # collection -> {id: (text, metadata)}  (survives "restarts")
        self.fail_next_model_load = False
        self.model_delay = 0.0
        self.store_delay = 0.0

    def restart_counters(self):
        """A new process: counters start from zero, persisted data stays."""

        self.model_constructed = 0
        self.model_import_accessed = 0
        self.model_names = []
        self.texts_encoded = 0
        self.chroma_created = []


def build_stub_modules(fakes):
    class FakeModel:
        """Stands in for HuggingFaceEmbeddings(...)."""

        def __init__(self, model_name=None, **kwargs):
            if fakes.model_delay:
                time.sleep(fakes.model_delay)
            if fakes.fail_next_model_load:
                fakes.fail_next_model_load = False
                raise RuntimeError("model download failed")
            with fakes.lock:
                fakes.model_constructed += 1
                fakes.model_names.append(model_name)

        @staticmethod
        def _vector(text):
            return [float(len(text)), float(sum(map(ord, text)) % 97)]

        def embed_documents(self, texts):
            with fakes.lock:
                fakes.texts_encoded += len(texts)
            return [self._vector(t) for t in texts]

        def embed_query(self, text):
            with fakes.lock:
                fakes.texts_encoded += 1
            return self._vector(text)

    class FakeChroma:
        def __init__(self, collection_name, embedding_function=None,
                     persist_directory=None, **kwargs):
            if fakes.store_delay:
                time.sleep(fakes.store_delay)
            with fakes.lock:
                fakes.chroma_created.append(collection_name)
                fakes.persist_dirs.add(persist_directory)
                fakes.stores.setdefault(collection_name, {})
            self._collection = collection_name
            self._embedding_function = embedding_function

        def _data(self):
            return fakes.stores[self._collection]

        def get(self, ids=None, where=None, include=None, **kwargs):
            rows = [
                (i, text, meta)
                for i, (text, meta) in list(self._data().items())
                if (ids is None or i in ids)
                and all(meta.get(k) == v for k, v in (where or {}).items())
            ]
            return {
                "ids": [r[0] for r in rows],
                "documents": [r[1] for r in rows],
                "metadatas": [r[2] for r in rows],
            }

        def add_texts(self, texts, metadatas=None, ids=None, **kwargs):
            texts = list(texts)
            self._embedding_function.embed_documents(texts)      # <- needs the model
            for text, meta, ident in zip(texts, metadatas or [{}] * len(texts), ids):
                self._data()[ident] = (text, dict(meta))
            return list(ids)

        def delete(self, ids=None, **kwargs):
            for ident in ids or []:
                self._data().pop(ident, None)

        def similarity_search(self, query, k=4, filter=None, **kwargs):
            self._embedding_function.embed_query(query)          # <- needs the model
            hits = self.get(where=filter)
            return [
                types.SimpleNamespace(page_content=t, metadata=m)
                for t, m in zip(hits["documents"], hits["metadatas"])
            ][:k]

    class HuggingFaceModule(types.ModuleType):
        """Counts how often the heavy `langchain_huggingface` import is attempted."""

        def __getattr__(self, name):
            if name == "HuggingFaceEmbeddings":
                with fakes.lock:
                    fakes.model_import_accessed += 1
                return FakeModel
            raise AttributeError(name)

    def module(name, **attributes):
        mod = types.ModuleType(name)
        mod.__dict__.update(attributes)
        return mod

    sqlalchemy = module("sqlalchemy", inspect=lambda engine: None, text=lambda sql: sql)
    sqlalchemy.exc = module("sqlalchemy.exc", SQLAlchemyError=Exception)
    langgraph = module("langgraph")
    langgraph.graph = module("langgraph.graph", StateGraph=object, START="__start__", END="__end__")

    return {
        "dotenv": module("dotenv", load_dotenv=lambda *a, **k: None),
        "sqlalchemy": sqlalchemy,
        "sqlalchemy.exc": sqlalchemy.exc,
        "langchain_google_genai": module(
            "langchain_google_genai", ChatGoogleGenerativeAI=lambda **kwargs: object()
        ),
        "langchain_chroma": module("langchain_chroma", Chroma=FakeChroma),
        "langchain_huggingface": HuggingFaceModule("langchain_huggingface"),
        "langgraph": langgraph,
        "langgraph.graph": langgraph.graph,
    }


class LazyBase(unittest.TestCase):
    """Imports the real app.queryclarify against fakes; supports simulated restarts."""

    def setUp(self):
        self.fakes = Fakes()
        self._saved_modules = {}
        self._provider = db_registry._builtin_provider
        self._model_loaded = lazy_resources._model_loaded

        self._env = env(
            GOOGLE_API_KEY=FAKE_API_KEY,
            MYSQL_PASSWORD=FAKE_PASSWORD,
            QC_BUILTIN_DATABASES=None,
            QC_BUILTIN_DATABASES_FILE=str(Path(_TMP) / "does-not-exist.txt"),
            QC_EMBEDDING_MIN_HEADROOM_MB="0",     # the guard has its own tests below
        )
        self._env.__enter__()
        self.addCleanup(self._cleanup)

        lazy_resources._model_loaded = False
        self._install_stubs()

    def _install_stubs(self):
        for name, stub in build_stub_modules(self.fakes).items():
            self._saved_modules.setdefault(name, sys.modules.get(name))
            sys.modules[name] = stub

    def _forget_queryclarify(self):
        sys.modules.pop("app.queryclarify", None)
        import app
        if hasattr(app, "queryclarify"):
            delattr(app, "queryclarify")

    def load(self):
        """Import app.queryclarify (a fresh 'process')."""

        self._forget_queryclarify()
        module = importlib.import_module("app.queryclarify")
        self.qc = module
        return module

    def restart(self):
        """Simulate a backend restart: new module state, same persisted Chroma data."""

        self.fakes.restart_counters()
        lazy_resources._model_loaded = False
        self._install_stubs()
        return self.load()

    def _cleanup(self):
        self._forget_queryclarify()
        for name, original in self._saved_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
        db_registry.set_builtin_provider(self._provider)
        db_registry.invalidate_builtin_cache()
        lazy_resources._model_loaded = self._model_loaded
        self._env.__exit__(None, None, None)

    def seed_builtin_index(self):
        """Pre-existing, 'restored' Chroma data: 2 built-in databases, 3 schema docs."""

        schema = self.fakes.stores.setdefault("spiderman_schema", {})
        for table, database in (("Student", "college_3"), ("Course", "college_3"), ("Cars", "car_1")):
            schema[f"{database}::{table}"] = (
                f"DATABASE: {database}\nTABLE: {table}\nCOLUMNS:\n- id (INTEGER)",
                {"database": database, "table": table},
            )

        router = self.fakes.stores.setdefault("spiderman_databases", {})
        for database in ("college_3", "car_1"):
            router[database] = (f"DATABASE: {database}", {"database": database})


# ===========================================================================
# 1. lazy initialization
# ===========================================================================

class LazyInitializationTests(LazyBase):
    def test_importing_the_app_loads_nothing(self):
        self.load()
        self.assertEqual(self.fakes.model_constructed, 0)
        self.assertEqual(self.fakes.model_import_accessed, 0, "the heavy import must not even run")
        self.assertEqual(self.fakes.chroma_created, [], "stores are lazy too")
        self.assertFalse(self.qc.get_embeddings().loaded)

    def test_creating_the_stores_does_not_load_the_model(self):
        qc = self.load()
        qc.get_vectorstore()
        qc.get_database_vectorstore()
        self.assertEqual(sorted(self.fakes.chroma_created), ["spiderman_databases", "spiderman_schema"])
        self.assertEqual(self.fakes.model_constructed, 0)
        self.assertEqual(self.fakes.model_import_accessed, 0)

    def test_stores_use_the_persisted_chroma_directory(self):
        qc = self.load()
        qc.get_vectorstore()
        self.assertEqual(self.fakes.persist_dirs, {qc.CHROMA_DIR})

    def test_model_loads_on_first_embedding_use_with_the_unchanged_model_name(self):
        qc = self.load()
        qc.get_vectorstore().add_texts(["hello"], [{"database": "x"}], ["x::1"])
        self.assertEqual(self.fakes.model_constructed, 1)
        self.assertEqual(self.fakes.model_names, [MODEL_NAME])     # same model => same vectors
        self.assertTrue(qc.get_embeddings().loaded)

    def test_repeated_access_reuses_the_same_objects_and_model(self):
        qc = self.load()
        stores = {id(qc.get_vectorstore()) for _ in range(50)}
        routers = {id(qc.get_database_vectorstore()) for _ in range(50)}
        self.assertEqual((len(stores), len(routers)), (1, 1))

        for i in range(5):
            qc.get_vectorstore().add_texts([f"t{i}"], [{"database": "x"}], [f"x::{i}"])
            qc.get_database_vectorstore().similarity_search("q")

        self.assertEqual(self.fakes.model_constructed, 1)
        self.assertEqual(self.fakes.chroma_created.count("spiderman_schema"), 1)
        self.assertEqual(self.fakes.chroma_created.count("spiderman_databases"), 1)

    def test_both_stores_share_one_embedding_instance(self):
        qc = self.load()
        shared = qc.get_embeddings()
        self.assertIs(qc.get_vectorstore()._embedding_function, shared)
        self.assertIs(qc.get_database_vectorstore()._embedding_function, shared)
        qc.get_vectorstore().add_texts(["a"], [{}], ["a"])
        qc.get_database_vectorstore().add_texts(["b"], [{}], ["b"])
        self.assertEqual(self.fakes.model_constructed, 1)

    def test_old_module_level_names_still_resolve_lazily(self):
        qc = self.load()
        self.assertEqual(self.fakes.chroma_created, [])
        self.assertIs(qc.vectorstore, qc.get_vectorstore())              # database_service.get_store()
        self.assertIs(qc.database_vectorstore, qc.get_database_vectorstore())
        self.assertIs(qc.embeddings, qc.get_embeddings())
        self.assertIs(database_service.get_store(), qc.get_vectorstore())
        self.assertEqual(self.fakes.model_constructed, 0)
        with self.assertRaises(AttributeError):
            qc.no_such_name  # noqa: B018

    def test_from_import_of_the_old_names_still_works(self):
        self.load()
        from app.queryclarify import vectorstore       # noqa: PLC0415
        self.assertIsNotNone(vectorstore)

    def test_a_failed_model_load_is_not_cached(self):
        qc = self.load()
        self.fakes.fail_next_model_load = True
        store = qc.get_vectorstore()
        with self.assertRaises(RuntimeError):
            store.add_texts(["a"], [{}], ["a"])
        self.assertFalse(qc.get_embeddings().loaded)
        store.add_texts(["a"], [{}], ["a"])                  # retry succeeds
        self.assertEqual(self.fakes.model_constructed, 1)
        self.assertTrue(qc.get_embeddings().loaded)


# ===========================================================================
# 2. concurrency
# ===========================================================================

class ConcurrencyTests(LazyBase):
    THREADS = 16

    def run_threads(self, work):
        barrier = threading.Barrier(self.THREADS)
        results, errors = [None] * self.THREADS, []

        def target(index):
            try:
                barrier.wait(timeout=10)
                results[index] = work(index)
            except BaseException as exc:      # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=target, args=(i,)) for i in range(self.THREADS)]
        [t.start() for t in threads]
        [t.join(timeout=30) for t in threads]
        self.assertEqual(errors, [])
        self.assertFalse(any(t.is_alive() for t in threads), "a thread deadlocked")
        return results

    def test_concurrent_first_access_builds_each_store_once(self):
        qc = self.load()
        self.fakes.store_delay = 0.05                     # widen the race window
        stores = self.run_threads(lambda i: (qc.get_vectorstore if i % 2 else qc.get_database_vectorstore)())
        self.assertEqual(self.fakes.chroma_created.count("spiderman_schema"), 1)
        self.assertEqual(self.fakes.chroma_created.count("spiderman_databases"), 1)
        self.assertEqual(len({id(s) for s in stores[0::2]}), 1)
        self.assertEqual(len({id(s) for s in stores[1::2]}), 1)
        self.assertEqual(self.fakes.model_constructed, 0)

    def test_concurrent_first_embedding_loads_the_model_once(self):
        qc = self.load()
        self.fakes.model_delay = 0.1                      # widen the race window
        self.run_threads(
            lambda i: qc.get_vectorstore().add_texts([f"t{i}"], [{"database": "x"}], [f"x::{i}"])
        )
        self.assertEqual(self.fakes.model_constructed, 1)
        self.assertEqual(self.fakes.model_import_accessed, 1)
        self.assertEqual(self.fakes.texts_encoded, self.THREADS)
        self.assertEqual(len(qc.get_vectorstore().get()["ids"]), self.THREADS)

    def test_concurrent_queries_and_indexing_share_one_model(self):
        qc = self.load()
        self.fakes.model_delay = 0.05

        def work(i):
            if i % 2:
                return len(qc.get_vectorstore().similarity_search("q", k=3))
            qc.get_database_vectorstore().add_texts([f"d{i}"], [{"database": f"d{i}"}], [f"d{i}"])
            return 0

        self.run_threads(work)
        self.assertEqual(self.fakes.model_constructed, 1)

    def test_concurrent_failure_then_recovery_still_loads_once(self):
        qc = self.load()
        self.fakes.fail_next_model_load = True
        store = qc.get_vectorstore()
        outcomes = []

        def work(i):
            try:
                store.add_texts([f"t{i}"], [{}], [f"t{i}"])
                outcomes.append("ok")
            except RuntimeError:
                outcomes.append("failed")

        self.run_threads(work)
        self.assertEqual(outcomes.count("failed"), 1)         # exactly the one that hit the failure
        self.assertEqual(outcomes.count("ok"), self.THREADS - 1)
        self.assertEqual(self.fakes.model_constructed, 1)


# ===========================================================================
# 3. built-in databases: the model is never needed
# ===========================================================================

class FakeInspector:
    def get_table_names(self):
        return ["Student", "Course"]

    def get_foreign_keys(self, table):
        return []


class BuiltinPathTests(LazyBase):
    def test_allow_list_comes_from_the_router_index_without_the_model(self):
        qc = self.load()
        self.seed_builtin_index()
        db_registry.invalidate_builtin_cache()
        self.assertEqual(db_registry.builtin_databases(), frozenset({"college_3", "car_1"}))
        self.assertEqual(self.fakes.model_constructed, 0)
        self.assertEqual(self.fakes.model_import_accessed, 0)

    def test_router_scoring_reads_stored_vectors_without_the_model(self):
        qc = self.load()
        self.seed_builtin_index()
        with contextlib.redirect_stdout(io.StringIO()):
            result = qc.database_router_node({"question": "show the students in college_3"})
        self.assertIn("database", result)
        self.assertEqual(self.fakes.model_constructed, 0)

    def test_schema_rag_for_a_builtin_query_never_loads_the_model(self):
        qc = self.load()
        self.seed_builtin_index()
        with mock.patch.object(qc, "get_database_engine", return_value=object()), \
                mock.patch.object(qc, "inspect", return_value=FakeInspector()), \
                contextlib.redirect_stdout(io.StringIO()):
            state = qc.rag_node({"question": "How many students are there?", "database": "college_3"})
        self.assertIn("TABLE: Student", state["schema"])
        self.assertNotIn("TABLE: Cars", state["schema"])                # isolated to college_3
        self.assertEqual(self.fakes.model_constructed, 0)
        self.assertEqual(self.fakes.model_import_accessed, 0)
        self.assertEqual(self.fakes.chroma_created, ["spiderman_schema"])

    def test_builtin_retrieval_scoring_for_builtin_databases_skips_semantic_search(self):
        qc = self.load()
        self.seed_builtin_index()
        scored = [(5, "Student", "doc", {"table": "Student"})]
        self.assertEqual(qc.apply_semantic_bonus("students", "college_3", scored), scored)
        self.assertEqual(self.fakes.model_constructed, 0)


# ===========================================================================
# 4. uploaded databases: first use, restart, cleanup
# ===========================================================================

class UploadedDatabaseTests(LazyBase):
    OWNER = db_registry.hash_owner("lazy-owner-client-0001")

    def csv(self, name, body):
        path = Path(tempfile.mkdtemp(dir=_TMP)) / name
        path.write_text(body)
        return path

    def test_indexing_works_on_first_use_and_after_a_restart(self):
        qc = self.load()
        self.seed_builtin_index()

        # ---- first use: the upload loads the model exactly once
        first = database_service.create_user_database(
            self.OWNER, "Shop", [self.csv("customers.csv", "id,name\n1,Ann\n2,Bob\n")]
        )
        first_id = first["database"]["id"]
        self.assertEqual(self.fakes.model_constructed, 1)
        indexed = qc.get_vectorstore().get(where={"database": first_id})
        self.assertEqual(len(indexed["ids"]), 1)
        self.assertIn("TABLE: customers", indexed["documents"][0])
        self.assertEqual(indexed["metadatas"][0]["kind"], "user")

        # ---- restart: nothing is loaded at boot, persisted data is still readable
        qc = self.restart()
        self.assertEqual(self.fakes.model_constructed, 0)
        again = qc.get_vectorstore().get(where={"database": first_id})
        self.assertEqual(again["ids"], indexed["ids"])
        self.assertEqual(self.fakes.model_constructed, 0, "reading stored vectors needs no model")

        # ---- after the restart a new upload loads the model once and indexes
        second = database_service.create_user_database(
            self.OWNER, "Orders", [self.csv("orders.csv", "oid,total\n1,9.5\n2,3\n")]
        )
        second_id = second["database"]["id"]
        self.assertEqual(self.fakes.model_constructed, 1)
        self.assertEqual(len(qc.get_vectorstore().get(where={"database": second_id})["ids"]), 1)

        # ---- isolation: each database only sees its own documents; built-ins untouched
        self.assertEqual(len(qc.get_vectorstore().get(where={"database": first_id})["ids"]), 1)
        self.assertEqual(len(qc.get_vectorstore().get(where={"database": "college_3"})["ids"]), 2)

        # ---- cleanup removes only the uploaded database's documents
        database_service.delete_user_database(first_id, self.OWNER)
        database_service.delete_user_database(second_id, self.OWNER)
        self.assertEqual(qc.get_vectorstore().get(where={"database": first_id})["ids"], [])
        self.assertEqual(len(qc.get_vectorstore().get(where={"database": "college_3"})["ids"]), 2)
        self.assertEqual(self.fakes.model_constructed, 1)          # deleting never embeds

    def test_semantic_retrieval_for_an_uploaded_database_works_after_a_restart(self):
        self.load()
        out = database_service.create_user_database(
            self.OWNER, "Shop", [self.csv("customers.csv", "id,name\n1,Ann\n")]
        )
        db_id = out["database"]["id"]
        self.addCleanup(lambda: database_service.delete_user_database(db_id, self.OWNER))

        qc = self.restart()
        self.assertEqual(self.fakes.model_constructed, 0)
        document = qc.get_vectorstore().get(where={"database": db_id})["documents"][0]
        scored = [(0, "customers", document, {"table": "customers", "kind": "user"})]

        boosted = qc.apply_semantic_bonus("show me the customers", db_id, scored)

        self.assertGreater(boosted[0][0], 0)               # the semantic bonus was applied
        self.assertEqual(self.fakes.model_constructed, 1)  # the model was loaded on demand

    def test_a_failed_indexing_leaves_nothing_behind_and_can_be_retried(self):
        qc = self.load()
        self.fakes.fail_next_model_load = True
        before = set(db_registry.USER_DB_DIR.glob("*")) if db_registry.USER_DB_DIR.exists() else set()
        with self.assertRaises(RuntimeError):
            database_service.create_user_database(
                self.OWNER, "Shop", [self.csv("customers.csv", "id,name\n1,Ann\n")]
            )
        after = set(db_registry.USER_DB_DIR.glob("*")) if db_registry.USER_DB_DIR.exists() else set()
        self.assertEqual(after, before)                     # file rolled back
        self.assertEqual(qc.get_vectorstore().get()["ids"], [])
        out = database_service.create_user_database(        # retry works
            self.OWNER, "Shop", [self.csv("customers.csv", "id,name\n1,Ann\n")]
        )
        self.addCleanup(lambda: database_service.delete_user_database(out["database"]["id"], self.OWNER))
        self.assertEqual(self.fakes.model_constructed, 1)


# ===========================================================================
# 5. memory guard
# ===========================================================================

MB = 1024 * 1024


class MemoryGuardTests(LazyBase):
    def test_refuses_when_the_model_would_not_fit(self):
        with self.assertRaises(lazy_resources.EmbeddingMemoryError) as ctx:
            lazy_resources.check_memory_headroom(500, limit_bytes=512 * MB, rss_bytes=250 * MB)
        self.assertIn("1-2 GB", str(ctx.exception))
        self.assertIn("512 MB", str(ctx.exception))

    def test_allows_when_there_is_enough_headroom(self):
        lazy_resources.check_memory_headroom(500, limit_bytes=2048 * MB, rss_bytes=300 * MB)
        lazy_resources.check_memory_headroom(500, limit_bytes=1024 * MB, rss_bytes=400 * MB)

    def test_unknown_limit_or_disabled_check_never_blocks(self):
        # None means "auto-detect", so the detectors are patched explicitly: the
        # result must not depend on the machine the tests happen to run on.
        with mock.patch.object(lazy_resources, "memory_limit_bytes", lambda *a, **k: None):
            lazy_resources.check_memory_headroom(500, rss_bytes=250 * MB)         # laptop / no limit
        with mock.patch.object(lazy_resources, "process_rss_bytes", lambda *a, **k: None):
            lazy_resources.check_memory_headroom(500, limit_bytes=512 * MB)       # RSS unreadable
        lazy_resources.check_memory_headroom(0, limit_bytes=512 * MB, rss_bytes=511 * MB)  # disabled

    def test_threshold_comes_from_the_environment(self):
        with env(QC_EMBEDDING_MIN_HEADROOM_MB="100"):
            lazy_resources.check_memory_headroom(None, limit_bytes=512 * MB, rss_bytes=250 * MB)
        with env(QC_EMBEDDING_MIN_HEADROOM_MB="not-a-number"):
            with self.assertRaises(lazy_resources.EmbeddingMemoryError):
                lazy_resources.check_memory_headroom(None, limit_bytes=512 * MB, rss_bytes=250 * MB)

    def test_cgroup_limit_parsing(self):
        folder = Path(tempfile.mkdtemp(dir=_TMP))

        def limit(text):
            path = folder / "limit"
            path.write_text(text)
            return lazy_resources.memory_limit_bytes([str(path)])

        self.assertEqual(limit("536870912\n"), 512 * MB)
        self.assertIsNone(limit("max\n"))                       # cgroup v2: unlimited
        self.assertIsNone(limit("9223372036854771712\n"))      # cgroup v1: unlimited sentinel
        self.assertIsNone(limit("garbage"))
        self.assertIsNone(lazy_resources.memory_limit_bytes([str(folder / "missing")]))

    def test_rss_parsing(self):
        status = Path(tempfile.mkdtemp(dir=_TMP)) / "status"
        status.write_text("Name:\tpython\nVmPeak:\t900 kB\nVmRSS:\t  262144 kB\nThreads:\t4\n")
        self.assertEqual(lazy_resources.process_rss_bytes(str(status)), 256 * MB)
        self.assertIsNone(lazy_resources.process_rss_bytes(str(status.parent / "nope")))

    def guard_failing(self):
        return mock.patch.multiple(
            lazy_resources,
            memory_limit_bytes=lambda *a, **k: 512 * MB,
            process_rss_bytes=lambda *a, **k: 300 * MB,
        )

    def test_a_refused_load_never_imports_the_heavy_libraries(self):
        qc = self.load()
        with env(QC_EMBEDDING_MIN_HEADROOM_MB="500"), self.guard_failing():
            with self.assertRaises(lazy_resources.EmbeddingMemoryError):
                qc.get_vectorstore().add_texts(["a"], [{}], ["a"])
        self.assertEqual(self.fakes.model_import_accessed, 0)     # torch was never imported
        self.assertEqual(self.fakes.model_constructed, 0)
        self.assertFalse(lazy_resources.embedding_model_loaded())

    def test_built_in_queries_keep_working_while_loading_is_refused(self):
        qc = self.load()
        self.seed_builtin_index()
        with env(QC_EMBEDDING_MIN_HEADROOM_MB="500"), self.guard_failing(), \
                mock.patch.object(qc, "get_database_engine", return_value=object()), \
                mock.patch.object(qc, "inspect", return_value=FakeInspector()), \
                contextlib.redirect_stdout(io.StringIO()):
            state = qc.rag_node({"question": "students", "database": "college_3"})
        self.assertIn("TABLE: Student", state["schema"])

    def test_uploaded_database_queries_fall_back_to_lexical_retrieval(self):
        qc = self.load()
        scored = [(3, "customers", "doc", {"table": "customers"})]
        db_id = "u_0123456789ab"
        with env(QC_EMBEDDING_MIN_HEADROOM_MB="500"), self.guard_failing(), \
                contextlib.redirect_stdout(io.StringIO()):
            result = qc.apply_semantic_bonus("customers", db_id, scored)
        self.assertEqual(result, scored)                          # unchanged, no crash

    def test_refusal_is_not_cached_and_loading_succeeds_when_memory_is_available(self):
        qc = self.load()
        store = qc.get_vectorstore()
        with env(QC_EMBEDDING_MIN_HEADROOM_MB="500"), self.guard_failing():
            with self.assertRaises(lazy_resources.EmbeddingMemoryError):
                store.add_texts(["a"], [{}], ["a"])
        store.add_texts(["a"], [{}], ["a"])                       # guard disabled in setUp
        self.assertEqual(self.fakes.model_constructed, 1)

    def test_fail_fast_check_is_a_no_op_once_the_model_is_loaded(self):
        qc = self.load()
        qc.get_vectorstore().add_texts(["a"], [{}], ["a"])
        with env(QC_EMBEDDING_MIN_HEADROOM_MB="500"), self.guard_failing():
            lazy_resources.require_embedding_headroom()           # must not raise
        qc.get_embeddings().unload()
        with env(QC_EMBEDDING_MIN_HEADROOM_MB="500"), self.guard_failing():
            with self.assertRaises(lazy_resources.EmbeddingMemoryError):
                lazy_resources.require_embedding_headroom()


# ===========================================================================
# 6. static guarantees (these catch a regression even where no fake exists)
# ===========================================================================

HEAVY = {"langchain_huggingface", "sentence_transformers", "torch", "transformers"}


def module_level_imports(path):
    """Imports that run when the module is imported (not inside functions)."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []

    def visit(nodes):
        for node in nodes:
            if isinstance(node, ast.Import):
                found.extend(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.append(node.module.split(".")[0])
            elif isinstance(node, (ast.If, ast.Try, ast.With)):
                for field in ("body", "orelse", "finalbody", "handlers"):
                    block = getattr(node, field, [])
                    visit(block if field != "handlers" else [n for h in block for n in h.body])

    visit(tree.body)
    return set(found)


class StaticGuaranteeTests(unittest.TestCase):
    def test_no_serving_module_imports_the_torch_stack_at_import_time(self):
        files = [ROOT / "app" / "queryclarify.py", ROOT / "app" / "lazy_resources.py"]
        files += list((ROOT / "backend").rglob("*.py"))
        for path in files:
            with self.subTest(path.relative_to(ROOT).as_posix()):
                self.assertEqual(module_level_imports(path) & HEAVY, set())

    def test_the_embedding_model_is_only_constructed_inside_the_lazy_loader(self):
        for path in (ROOT / "app" / "queryclarify.py", *(ROOT / "backend").rglob("*.py")):
            self.assertNotIn("HuggingFaceEmbeddings(", path.read_text(encoding="utf-8"), path.name)
        source = (ROOT / "app" / "lazy_resources.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("HuggingFaceEmbeddings("), 1)
        self.assertIn(f'EMBEDDING_MODEL_NAME = "{MODEL_NAME}"', source)

    def test_queryclarify_never_uses_the_eager_names_internally(self):
        source = (ROOT / "app" / "queryclarify.py").read_text(encoding="utf-8")
        import re
        self.assertEqual(re.findall(r"(?<![\w.])(?:database_)?vectorstore\.(?:get|similarity_search)", source), [])

    def test_upload_route_fails_fast_and_maps_memory_errors_to_503(self):
        source = (ROOT / "backend" / "api" / "database.py").read_text(encoding="utf-8")
        route = source[source.index("def upload_database("):source.index("# Delete one of the caller")]

        # 1) fail fast: checked, and answered with 503, BEFORE the upload is read
        fail_fast = route.index("require_embedding_headroom()")
        self.assertLess(fail_fast, route.index("_save_uploads("))
        self.assertLess(fail_fast, route.index("except EmbeddingMemoryError"))

        # 2) a second, separate handler guards the indexing itself (the model can
        #    still be refused there): after create_user_database(...) and before
        #    the catch-all that would report a generic 500
        indexing = route.index("create_user_database(")
        handlers = [i for i in range(len(route)) if route.startswith("except EmbeddingMemoryError", i)]
        self.assertEqual(len(handlers), 2, "both the fail-fast and the indexing handler must exist")
        self.assertGreater(handlers[1], indexing)
        self.assertLess(handlers[1], route.index("except Exception:"))
        self.assertEqual(route.count("status_code=503"), 2)


def tearDownModule():
    import shutil
    shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
