"""
Shared helpers for the security/hygiene tests.

Every credential used in tests is OBVIOUSLY FAKE (prefix ``FAKE-``): the secret
scanner treats such values as safe, and nobody can mistake them for real
secrets. No MySQL, SQLAlchemy or LangChain is needed.
"""

import contextlib
import logging
import os
import sys
import types

FAKE_PASSWORD = "FAKE-test-password-not-real"
FAKE_RO_PASSWORD = "FAKE-readonly-password-not-real"
FAKE_ADMIN_PASSWORD = "FAKE-admin-password-not-real"
FAKE_API_KEY = "FAKE-api-key-not-real"


@contextlib.contextmanager
def env(**values):
    """Temporarily set (or, with None, remove) environment variables."""

    saved = {key: os.environ.get(key) for key in values}

    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class CaptureLogs:
    """Capture every log line AS A HANDLER WOULD FORMAT IT (tracebacks included)."""

    def __enter__(self):
        outer = self
        formatter = logging.Formatter("%(name)s %(levelname)s %(message)s")
        self.lines = []

        class Handler(logging.Handler):
            def emit(self, record):
                outer.lines.append(formatter.format(record))

        self._handler = Handler(level=logging.DEBUG)
        root = logging.getLogger()
        self._level = root.level
        root.addHandler(self._handler)
        root.setLevel(logging.DEBUG)
        return self

    def __exit__(self, *exc):
        root = logging.getLogger()
        root.removeHandler(self._handler)
        root.setLevel(self._level)
        return False

    @property
    def text(self):
        return "\n".join(self.lines)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        if self._engine.fail_with is not None:
            raise self._engine.fail_with
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement, params=None):
        text = str(statement)
        self._engine.executed.append(text)

        for needle, rows in self._engine.rows_by_sql.items():
            if needle in text:
                return FakeResult(rows)

        return FakeResult([])


class FakeEngine:
    """Engine whose connect() serves canned rows keyed by a SQL substring."""

    def __init__(self, rows_by_sql=None, fail_with=None):
        self.rows_by_sql = rows_by_sql or {}
        self.fail_with = fail_with
        self.executed = []

    def connect(self):
        return FakeConnection(self)


class StubSqlAlchemy:
    """Installs a fake ``sqlalchemy`` (create_engine, event, text, engine.URL)."""

    def __enter__(self):
        self.created, self.listeners, self.url_calls = [], [], []
        outer = self

        class StubEngine:
            def __init__(self, url, kwargs):
                self.url, self.kwargs, self.disposed = url, kwargs, False

            def dispose(self):
                self.disposed = True

        def create_engine(url, **kwargs):
            engine = StubEngine(url, kwargs)
            outer.created.append(engine)
            return engine

        def listens_for(target, name):
            def decorator(fn):
                outer.listeners.append((target, name, fn))
                return fn

            return decorator

        class FakeUrl:
            """Like sqlalchemy.engine.URL: str()/repr() mask the password."""

            def __init__(self, drivername, username, password, host, port, database):
                self.drivername, self.username, self.password = drivername, username, password
                self.host, self.port, self.database = host, port, database

            @classmethod
            def create(cls, drivername, username=None, password=None, host=None,
                       port=None, database=None):
                outer.url_calls.append(
                    dict(drivername=drivername, username=username, password=password,
                         host=host, port=port, database=database)
                )
                return cls(drivername, username, password, host, port, database)

            def __str__(self):
                return (f"{self.drivername}://{self.username}:***@{self.host}:"
                        f"{self.port}/{self.database}")

            __repr__ = __str__

        module = types.ModuleType("sqlalchemy")
        module.create_engine = create_engine
        module.event = types.SimpleNamespace(listens_for=listens_for)
        module.text = lambda sql: sql
        engine_module = types.ModuleType("sqlalchemy.engine")
        engine_module.URL = FakeUrl
        module.engine = engine_module

        self._saved = (sys.modules.get("sqlalchemy"), sys.modules.get("sqlalchemy.engine"))
        sys.modules["sqlalchemy"] = module
        sys.modules["sqlalchemy.engine"] = engine_module
        return self

    def __exit__(self, *exc):
        for name, saved in zip(("sqlalchemy", "sqlalchemy.engine"), self._saved):
            if saved is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = saved
        return False


class FakeCursor:
    def __init__(self, fail_on=()):
        self.fail_on, self.executed, self.closed = tuple(fail_on), [], False

    def execute(self, statement):
        self.executed.append(statement)
        if any(token in statement for token in self.fail_on):
            raise RuntimeError("Unknown system variable")

    def close(self):
        self.closed = True


class FakeDbapiConnection:
    def __init__(self, fail_on=()):
        self.cur = FakeCursor(fail_on)

    def cursor(self):
        return self.cur
