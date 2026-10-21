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
MODEL_NAME = "gemini-embedding-001"


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

        def __init__(self, model_name=None,model = None , **kwargs):
            if fakes.model_delay:
                time.sleep(fakes.model_delay)
            if fakes.fail_next_model_load:
                fakes.fail_next_model_load = False
                raise RuntimeError("model download failed")
            with fakes.lock:
                fakes.model_constructed += 1
                fakes.model_names.append(model or model_name)

        @staticmethod
        def _vector(text):
            return [float(len(text)), float(sum(map(ord, text)) % 97)]

        def embed_documents(self, texts, **kwargs):
            with fakes.lock:
                fakes.texts_encoded += len(texts)
            return [self._vector(t) for t in texts]

        def embed_query(self, text, **kwargs):
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
            "langchain_google_genai",
            ChatGoogleGenerativeAI=lambda **kwargs: object(),
            GoogleGenerativeAIEmbeddings=FakeModel,
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


        self._env = env(
            GOOGLE_API_KEY=FAKE_API_KEY,
            MYSQL_PASSWORD=FAKE_PASSWORD,
            QC_BUILTIN_DATABASES=None,
            QC_BUILTIN_DATABASES_FILE=str(Path(_TMP) / "does-not-exist.txt"),
            QC_EMBEDDING_MIN_HEADROOM_MB="0",     # the guard has its own tests below
        )
        self._env.__enter__()
        self.addCleanup(self._cleanup)


        self._install_stubs()

    def _install_stubs(self):
        for name, stub in build_stub_modules(self.fakes).items():
            self._saved_modules.setdefault(name, sys.modules.get(name))
            sys.modules[name] = stub

        lazy_resources.GoogleGenerativeAIEmbeddings = (
            sys.modules["langchain_google_genai"].GoogleGenerativeAIEmbeddings
        )

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

        self._env.__exit__(None, None, None)

    def seed_builtin_index(self):
        """Pre-existing, 'restored' Chroma data: 2 built-in databases, 3 schema docs."""

        schema = self.fakes.stores.setdefault("spiderman_schema_gemini", {})
        for table, database in (("Student", "college_3"), ("Course", "college_3"), ("Cars", "car_1")):
            schema[f"{database}::{table}"] = (
                f"DATABASE: {database}\nTABLE: {table}\nCOLUMNS:\n- id (INTEGER)",
                {"database": database, "table": table},
            )

        router = self.fakes.stores.setdefault("spiderman_databases_gemini", {})
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
        self.assertEqual(sorted(self.fakes.chroma_created), ["spiderman_databases_gemini", "spiderman_schema_gemini"])
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
        self.assertEqual(self.fakes.chroma_created.count("spiderman_schema_gemini"), 1)
        self.assertEqual(self.fakes.chroma_created.count("spiderman_databases_gemini"), 1)

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
        self.assertEqual(self.fakes.chroma_created.count("spiderman_schema_gemini"), 1)
        self.assertEqual(self.fakes.chroma_created.count("spiderman_databases_gemini"), 1)
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
        self.assertEqual(self.fakes.model_constructed, 1)
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
        self.assertEqual(self.fakes.chroma_created, ["spiderman_schema_gemini"])

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
    def test_builtin_queries_work_without_loading_embeddings(self):
        qc = self.load()
        self.seed_builtin_index()

        with (
            mock.patch.object(qc, "get_database_engine", return_value=object()),
            mock.patch.object(qc, "inspect", return_value=FakeInspector()),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            state = qc.rag_node({
                "question": "How many students are there?",
                "database": "college_3",
            })

        self.assertIn("TABLE: Student", state["schema"])
        self.assertEqual(self.fakes.model_constructed, 0)

    def test_embedding_client_failure_can_be_retried(self):
        qc = self.load()
        store = qc.get_vectorstore()
        self.fakes.fail_next_model_load = True

        with self.assertRaises(RuntimeError):
            store.add_texts(["test"], [{}], ["test"])

        store.add_texts(["test"], [{}], ["test"])
        self.assertEqual(self.fakes.model_constructed, 1)

    def test_remote_embeddings_do_not_require_local_memory_guard(self):
        self.assertTrue(callable(lazy_resources.require_embedding_headroom))
        self.assertIsNone(lazy_resources.require_embedding_headroom())



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
        self.assertEqual(source.count("GoogleGenerativeAIEmbeddings("), 1)
        self.assertIn('EMBEDDING_MODEL_NAME = "gemini-embedding-001"', source)
    def test_queryclarify_never_uses_the_eager_names_internally(self):
        source = (ROOT / "app" / "queryclarify.py").read_text(encoding="utf-8")
        import re
        self.assertEqual(re.findall(r"(?<![\w.])(?:database_)?vectorstore\.(?:get|similarity_search)", source), [])



def tearDownModule():
    import shutil
    shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
