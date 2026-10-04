"""
Tests for hardening the built-in MySQL path:

  * the built-in database ALLOW-LIST (db_registry)
  * dangerous-SQL guardrails (INTO OUTFILE, LOAD_FILE, SLEEP, ...)
  * MySQL session limits, credentials, engine factory
  * least-privilege grant generator and admin CLI

No MySQL, SQLAlchemy or LangChain needed:  python -m unittest discover -s tests
"""

import contextlib
import io
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="qc_hardening_")
os.environ.setdefault("QC_DATA_DIR", _TMP)

from app import db_registry, hardening_tools, mysql_hardening  # noqa: E402
from app.guardrails import validate_sql_guardrail  # noqa: E402

# Obviously fake test values (the secret scanner treats "FAKE-" values as safe).
FAKE_PASSWORD = "FAKE-test-password-not-real"
FAKE_RO_PASSWORD = "FAKE-p@ss/word-not-real"

ENV_KEYS = [
    "QC_BUILTIN_DATABASES", "QC_BUILTIN_DATABASES_FILE",
    "MYSQL_RO_USER", "MYSQL_RO_PASSWORD", "MYSQL_USER", "MYSQL_PASSWORD",
    "QC_MYSQL_MAX_EXECUTION_MS",
]


class EnvCase(unittest.TestCase):
    """Isolates env vars, the allow-list cache and its provider."""

    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        self._provider = db_registry._builtin_provider
        self._enabled = db_registry.BUILTIN_ENABLED
        db_registry.set_builtin_provider(None)
        mysql_hardening.forget_mysql_engines()

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        db_registry.BUILTIN_ENABLED = self._enabled
        db_registry.set_builtin_provider(self._provider)
        mysql_hardening.forget_mysql_engines()


# ---------------------------------------------------------------------------
# allow-list
# ---------------------------------------------------------------------------

class AllowListTests(EnvCase):
    def test_fails_closed_when_nothing_configured(self):
        self.assertEqual(db_registry.builtin_databases(), frozenset())
        self.assertFalse(db_registry.is_builtin_allowed("college_3"))
        with self.assertRaises(db_registry.DatabaseAccessError):
            db_registry.check_access("college_3", "")

    def test_env_source_normalises_names(self):
        os.environ["QC_BUILTIN_DATABASES"] = " College_3 , `car_1`,,"
        db_registry.invalidate_builtin_cache()
        self.assertEqual(
            db_registry.builtin_databases(), frozenset({"college_3", "car_1"})
        )
        self.assertTrue(db_registry.is_builtin_allowed("COLLEGE_3"))

    def test_file_source_with_comments(self):
        f = Path(_TMP) / "allow.txt"
        f.write_text("# header\nflight_2  # trailing comment\n\ncar_1\n")
        os.environ["QC_BUILTIN_DATABASES_FILE"] = str(f)
        db_registry.invalidate_builtin_cache()
        self.assertEqual(
            db_registry.builtin_databases(), frozenset({"flight_2", "car_1"})
        )

    def test_priority_env_then_file_then_provider(self):
        f = Path(_TMP) / "allow2.txt"
        f.write_text("from_file\n")
        os.environ["QC_BUILTIN_DATABASES_FILE"] = str(f)
        db_registry.set_builtin_provider(lambda: ["from_provider"])
        self.assertEqual(db_registry.builtin_databases(), frozenset({"from_file"}))
        os.environ["QC_BUILTIN_DATABASES"] = "from_env"
        db_registry.invalidate_builtin_cache()
        self.assertEqual(db_registry.builtin_databases(), frozenset({"from_env"}))
        del os.environ["QC_BUILTIN_DATABASES"]
        f.unlink()
        db_registry.invalidate_builtin_cache()
        self.assertEqual(
            db_registry.builtin_databases(), frozenset({"from_provider"})
        )

    def test_system_schemas_can_never_be_allow_listed(self):
        os.environ["QC_BUILTIN_DATABASES"] = (
            "mysql,information_schema,performance_schema,sys,college_3"
        )
        db_registry.invalidate_builtin_cache()
        self.assertEqual(db_registry.builtin_databases(), frozenset({"college_3"}))
        for name in ("mysql", "information_schema", "performance_schema", "sys",
                     "MYSQL", "`sys`"):
            self.assertFalse(db_registry.is_builtin_allowed(name), name)
            with self.assertRaises(db_registry.DatabaseAccessError):
                db_registry.check_access(name, "")

    def test_provider_cannot_smuggle_system_or_user_id_names(self):
        db_registry.set_builtin_provider(
            lambda: ["college_3", "mysql", "u_0123456789ab", "bad name", "x;y", ""]
        )
        self.assertEqual(db_registry.builtin_databases(), frozenset({"college_3"}))

    def test_provider_failure_fails_closed_then_keeps_last_good_list(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                return ["college_3"]
            raise RuntimeError("chroma down")

        db_registry.set_builtin_provider(flaky)
        self.assertEqual(db_registry.builtin_databases(), frozenset({"college_3"}))
        # force a refresh: the failing provider must not wipe the known list
        with db_registry._builtin_lock:
            db_registry._builtin_cache = (0.0, db_registry._builtin_cache[1])
        with self.assertLogs("queryclarify.registry", level="ERROR"):
            self.assertEqual(
                db_registry.builtin_databases(), frozenset({"college_3"})
            )

    def test_provider_failure_with_no_history_is_empty(self):
        def boom():
            raise RuntimeError("chroma down")

        db_registry.set_builtin_provider(boom)
        with self.assertLogs("queryclarify.registry", level="ERROR"):
            self.assertEqual(db_registry.builtin_databases(), frozenset())

    def test_cache_is_used_and_can_be_invalidated(self):
        calls = {"n": 0}

        def provider():
            calls["n"] += 1
            return ["college_3"]

        db_registry.set_builtin_provider(provider)
        for _ in range(5):
            db_registry.builtin_databases()
        self.assertEqual(calls["n"], 1)
        db_registry.invalidate_builtin_cache()
        db_registry.builtin_databases()
        self.assertEqual(calls["n"], 2)

    def test_builtin_disabled_blocks_everything(self):
        os.environ["QC_BUILTIN_DATABASES"] = "college_3"
        db_registry.invalidate_builtin_cache()
        db_registry.BUILTIN_ENABLED = False
        self.assertFalse(db_registry.is_builtin_allowed("college_3"))
        with self.assertRaises(db_registry.DatabaseAccessError):
            db_registry.check_access("college_3", "")


# ---------------------------------------------------------------------------
# SQL guardrail
# ---------------------------------------------------------------------------

def blocked(sql, db="college_3"):
    return not validate_sql_guardrail(sql, db).allowed


class DangerousSqlTests(unittest.TestCase):
    MUST_BLOCK = {
        # --- every construct named in the requirements
        "into outfile (no dot in path)": "SELECT * FROM Student INTO OUTFILE '/tmp/dump';",
        "into dumpfile": "SELECT 'a' INTO DUMPFILE '/tmp/y';",
        "load_file": "SELECT LOAD_FILE('/etc/passwd');",
        "load data": "LOAD DATA INFILE '/tmp/x' INTO TABLE Student;",
        "sleep": "SELECT SLEEP(3600);",
        "benchmark": "SELECT BENCHMARK(1000000000, MD5('a'));",
        "get_lock": "SELECT GET_LOCK('x', 600);",
        "release_lock": "SELECT RELEASE_LOCK('x');",
        # --- related primitives
        "release_all_locks": "SELECT RELEASE_ALL_LOCKS();",
        "is_free_lock": "SELECT IS_FREE_LOCK('x');",
        "into @var": "SELECT 1 INTO @a;",
        "system variable": "SELECT @@datadir;",
        "user variable": "SELECT @a := 1;",
        "for update": "SELECT * FROM Student FOR UPDATE;",
        "lock in share mode": "SELECT * FROM Student LOCK IN SHARE MODE;",
        "user()": "SELECT USER();",
        "current_user": "SELECT CURRENT_USER;",
        "version()": "SELECT VERSION();",
        "database()": "SELECT DATABASE();",
        "schema()": "SELECT SCHEMA();",
        "connection_id": "SELECT CONNECTION_ID();",
        # --- hidden in expressions / subqueries
        "sleep in subquery": "SELECT * FROM Student WHERE StuID IN (SELECT SLEEP(1));",
        "sleep in derived table": "SELECT * FROM (SELECT SLEEP(5)) t;",
        "sleep in IF": "SELECT IF(1=1, SLEEP(5), 0);",
        "sleep in where": "SELECT * FROM Student WHERE SLEEP(5) = 0;",
        # --- obfuscation
        "mixed case + space": "SELECT sLeEp (5);",
        "newline before paren": "SELECT LOAD_FILE\n('/etc/passwd');",
        "tab before paren": "SELECT SLEEP\t(5);",
        "newline in into outfile": "SELECT * FROM Student INTO\nOUTFILE '/tmp/z';",
        "comment splitting": "SELECT SLEEP/**/(5);",
        # --- parser-differential tricks that used to pass
        "backtick-quote hides sleep": "SELECT `a'` , SLEEP(5) , 'b' FROM Student",
        "backslash-quote hides sleep": "SELECT 'a\\'' , SLEEP(5) , 'b' FROM Student",
        "backtick-quote hides into outfile": "SELECT `a'` INTO OUTFILE '/tmp/x' , 'b' FROM Student",
        "unterminated single quote": "SELECT 'abc FROM Student",
        "unterminated double quote": 'SELECT "abc FROM Student',
        "unterminated backtick": "SELECT `abc FROM Student",
        # --- system schemas
        "mysql.user": "SELECT * FROM mysql.user;",
        "quoted mysql.user": "SELECT * FROM `mysql`.`user`;",
        "info schema": "SELECT * FROM information_schema.tables;",
        "info schema spaced dot": "SELECT * FROM information_schema . tables;",
        "perf schema": "SELECT * FROM performance_schema.threads;",
        "sys schema": "SELECT * FROM sys.session;",
        "info schema unqualified": "SELECT * FROM information_schema;",
        "info schema via backticks only": "SELECT * FROM `information_schema`;",
        "comma join to system db": "SELECT * FROM Student, mysql.user;",
        "other database": "SELECT * FROM car_1.cars_data;",
        # --- baseline write/DDL
        "drop": "DROP TABLE Student;",
        "stacked": "SELECT 1; DROP TABLE Student;",
        "union": "SELECT 1 UNION SELECT 2;",
        "comment": "SELECT 1 /* x */;",
        "version comment": "SELECT /*!50000 1 */ 1;",
    }

    def test_all_dangerous_sql_is_blocked(self):
        for name, sql in self.MUST_BLOCK.items():
            with self.subTest(name):
                result = validate_sql_guardrail(sql, "college_3")
                self.assertFalse(result.allowed, f"{name}: {sql!r} was ALLOWED")

    def test_blocked_for_the_right_reason(self):
        expected = {
            "SELECT SLEEP(5);": "dangerous_sql",
            "SELECT * FROM Student INTO OUTFILE '/tmp/dump';": "dangerous_sql",
            "SELECT LOAD_FILE('/x');": "dangerous_sql",
            "SELECT * FROM mysql.user;": "system_database",
            "SELECT `a'` , 1 , 'b' FROM Student WHERE SLEEP(1)": "dangerous_sql",
            "SELECT 'a\\'' FROM Student": "invalid_sql",
            "SELECT 'abc FROM Student": "invalid_sql",
        }
        for sql, category in expected.items():
            with self.subTest(sql):
                self.assertEqual(
                    validate_sql_guardrail(sql, "college_3").category, category
                )

    def test_system_schemas_blocked_as_the_active_database(self):
        # No qualified names at all: only the active-database check can stop these.
        for db in ("mysql", "information_schema", "performance_schema", "sys",
                   "MYSQL", "Information_Schema"):
            for sql in ("SELECT User, authentication_string FROM user;",
                        "SELECT table_name FROM tables;"):
                with self.subTest(db=db, sql=sql):
                    r = validate_sql_guardrail(sql, db)
                    self.assertFalse(r.allowed)
                    self.assertEqual(r.category, "system_database")

    def test_legitimate_queries_still_pass(self):
        allowed = [
            "SELECT count(*) FROM Student;",
            "SELECT Fname, LName FROM Student WHERE Age > 20 ORDER BY LName;",
            "SELECT Fname FROM Student WHERE LName = 'O''Brien';",
            "SELECT T1.Fname FROM Student AS T1 JOIN Enrolled_in AS T2 ON T1.StuID = T2.StuID;",
            "SELECT city_code, COUNT(*) FROM Student GROUP BY city_code HAVING COUNT(*) > 1;",
            "WITH c AS (SELECT * FROM Student) SELECT * FROM c;",
            # dangerous words that are only DATA or quoted identifiers
            "SELECT * FROM Student WHERE Fname LIKE '%sleep(%';",
            "SELECT * FROM Student WHERE Fname = 'go into the woods';",
            "SELECT * FROM Student WHERE Fname = 'load_file(x)';",
            "SELECT `Sleep`, `user`, `into`, `version` FROM Student;",
            "SELECT sleep_hours, user_name, version_no FROM Student;",
            "SELECT `a'` , 1 , 'b' FROM Student;",
        ]
        for sql in allowed:
            with self.subTest(sql):
                r = validate_sql_guardrail(sql, "college_3")
                self.assertTrue(r.allowed, f"{sql!r} wrongly blocked: {r.reason}")

    def test_sqlite_user_databases_unaffected_by_mysql_backslash_rule(self):
        uid = "u_0123456789ab"
        # SQLite has no backslash escapes; this is a complete string.
        self.assertTrue(validate_sql_guardrail("SELECT '\\' FROM t;", uid).allowed)
        # ...but the dangerous constructs are blocked there too.
        self.assertTrue(blocked("SELECT SLEEP(1);", uid))
        self.assertTrue(blocked("SELECT load_extension('x');", uid))


# ---------------------------------------------------------------------------
# credentials + session limits
# ---------------------------------------------------------------------------

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


class SessionLimitTests(EnvCase):
    def test_mysql_applies_read_only_and_max_execution_time(self):
        conn = FakeDbapiConnection()
        applied = mysql_hardening.apply_session_limits(conn)
        self.assertEqual(applied, ["read_only", "statement_timeout", "row_cap"])
        self.assertEqual(conn.cur.executed[0], "SET SESSION TRANSACTION READ ONLY")
        self.assertIn("MAX_EXECUTION_TIME = 10000", conn.cur.executed[1])
        self.assertEqual(conn.cur.executed[2], "SET SESSION sql_select_limit = 1001")
        self.assertEqual(len(conn.cur.executed), 3)   # MariaDB fallback not needed
        self.assertTrue(conn.cur.closed)

    def test_mariadb_falls_back_to_max_statement_time(self):
        conn = FakeDbapiConnection(fail_on=["MAX_EXECUTION_TIME"])
        applied = mysql_hardening.apply_session_limits(conn)
        self.assertEqual(applied, ["read_only", "statement_timeout", "row_cap"])
        self.assertTrue(any("max_statement_time = 10" in q for q in conn.cur.executed))
        self.assertIn("sql_select_limit", conn.cur.executed[-1])

    def test_timeout_is_configurable(self):
        os.environ["QC_MYSQL_MAX_EXECUTION_MS"] = "2500"
        conn = FakeDbapiConnection()
        mysql_hardening.apply_session_limits(conn)
        self.assertIn("MAX_EXECUTION_TIME = 2500", conn.cur.executed[1])

    def test_unsupported_server_never_breaks_the_connection(self):
        conn = FakeDbapiConnection(fail_on=["SET SESSION"])
        with self.assertLogs("queryclarify.mysql", level="WARNING"):
            applied = mysql_hardening.apply_session_limits(conn)
        self.assertEqual(applied, [])
        self.assertTrue(conn.cur.closed)

    def test_client_socket_timeouts(self):
        args = mysql_hardening.connect_args()
        self.assertEqual(
            set(args), {"connect_timeout", "read_timeout", "write_timeout"}
        )
        self.assertTrue(all(v > 0 for v in args.values()))


class CredentialTests(EnvCase):
    def test_legacy_pair_is_default(self):
        os.environ["MYSQL_PASSWORD"] = FAKE_PASSWORD
        self.assertEqual(
            mysql_hardening.mysql_credentials(), ("root", FAKE_PASSWORD, False)
        )
        self.assertTrue(mysql_hardening.credentials_configured())

    def test_read_only_account_wins(self):
        os.environ.update(MYSQL_USER="root", MYSQL_PASSWORD=FAKE_PASSWORD,
                          MYSQL_RO_USER="qc_readonly", MYSQL_RO_PASSWORD=FAKE_RO_PASSWORD)
        self.assertEqual(
            mysql_hardening.mysql_credentials(), ("qc_readonly", FAKE_RO_PASSWORD, True)
        )

    def test_not_configured(self):
        self.assertFalse(mysql_hardening.credentials_configured())
        os.environ["MYSQL_RO_USER"] = "qc_readonly"          # user but no password
        self.assertFalse(mysql_hardening.credentials_configured())

    def test_security_warnings(self):
        os.environ["QC_BUILTIN_DATABASES"] = "college_3"
        db_registry.invalidate_builtin_cache()
        os.environ["MYSQL_PASSWORD"] = FAKE_PASSWORD
        warnings = mysql_hardening.security_warnings()
        self.assertTrue(any("root" in w for w in warnings))

        os.environ.update(MYSQL_RO_USER="qc_readonly", MYSQL_RO_PASSWORD=FAKE_RO_PASSWORD)
        self.assertEqual(mysql_hardening.security_warnings(), [])

        os.environ.pop("QC_BUILTIN_DATABASES")
        db_registry.invalidate_builtin_cache()
        self.assertTrue(
            any("allow-list is empty" in w for w in mysql_hardening.security_warnings())
        )

        db_registry.BUILTIN_ENABLED = False
        self.assertEqual(mysql_hardening.security_warnings(), [])


# ---------------------------------------------------------------------------
# engine factory (SQLAlchemy stubbed)
# ---------------------------------------------------------------------------

class StubSqlAlchemy:
    """Installs a fake `sqlalchemy` that records create_engine() calls."""

    def __enter__(self):
        self.created, self.listeners = [], []
        outer = self

        class FakeEngine:
            def __init__(self, url, kwargs):
                self.url, self.kwargs, self.disposed = url, kwargs, False

            def dispose(self):
                self.disposed = True

        def create_engine(url, **kwargs):
            engine = FakeEngine(url, kwargs)
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

        self.url_calls = []
        module = types.ModuleType("sqlalchemy")
        module.create_engine = create_engine
        module.event = types.SimpleNamespace(listens_for=listens_for)
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


class EngineFactoryTests(EnvCase):
    def setUp(self):
        super().setUp()
        os.environ["QC_BUILTIN_DATABASES"] = "college_3,car_1"
        os.environ.update(MYSQL_RO_USER="qc_readonly", MYSQL_RO_PASSWORD=FAKE_RO_PASSWORD)
        db_registry.invalidate_builtin_cache()

    def test_allow_listed_database_gets_hardened_engine(self):
        with StubSqlAlchemy() as sa:
            engine = mysql_hardening.get_mysql_engine("College_3")
            self.assertEqual(len(sa.created), 1)
            url = engine.url
            # Built from parts with URL.create (no string URL is ever parsed, so a
            # malformed host can never make SQLAlchemy echo the credentials).
            self.assertEqual(sa.url_calls[0]["drivername"], "mysql+pymysql")
            self.assertEqual(sa.url_calls[0]["username"], "qc_readonly")
            self.assertEqual(sa.url_calls[0]["password"], FAKE_RO_PASSWORD)  # raw: SQLAlchemy encodes
            self.assertEqual(sa.url_calls[0]["database"], "college_3")
            self.assertFalse(isinstance(url, str))
            # ...and the password never appears in its string / repr form.
            self.assertNotIn(FAKE_RO_PASSWORD, str(url))
            self.assertNotIn(FAKE_RO_PASSWORD, repr(url))
            self.assertIn("***", str(url))
            self.assertEqual(engine.kwargs["connect_args"], mysql_hardening.connect_args())
            self.assertTrue(engine.kwargs["pool_pre_ping"])
            # a "connect" listener that applies the session limits is registered
            self.assertEqual([(t is engine, n) for t, n, _ in sa.listeners], [(True, "connect")])
            conn = FakeDbapiConnection()
            sa.listeners[0][2](conn, None)
            self.assertEqual(conn.cur.executed[0], "SET SESSION TRANSACTION READ ONLY")

    def test_engines_are_cached_per_database(self):
        with StubSqlAlchemy() as sa:
            a = mysql_hardening.get_mysql_engine("college_3")
            b = mysql_hardening.get_mysql_engine("college_3")
            c = mysql_hardening.get_mysql_engine("car_1")
            self.assertIs(a, b)
            self.assertIsNot(a, c)
            self.assertEqual(len(sa.created), 2)

    def test_non_allow_listed_names_never_create_an_engine(self):
        with StubSqlAlchemy() as sa:
            for name in ("mysql", "information_schema", "performance_schema", "sys",
                         "random_db", "u_0123456789ab", "", "a;b", "x" * 80):
                with self.subTest(name):
                    with self.assertRaises(ValueError):
                        mysql_hardening.get_mysql_engine(name)
            self.assertEqual(sa.created, [])
            self.assertEqual(mysql_hardening._engines, {})   # no cache growth

    def test_error_does_not_reveal_whether_a_database_exists(self):
        with StubSqlAlchemy():
            messages = set()
            for name in ("mysql", "does_not_exist"):
                with self.assertRaises(ValueError) as ctx:
                    mysql_hardening.get_mysql_engine(name)
                messages.add(str(ctx.exception))
            self.assertEqual(len(messages), 1)

    def test_system_schema_refused_even_if_listed_in_env(self):
        os.environ["QC_BUILTIN_DATABASES"] = "mysql,college_3"
        db_registry.invalidate_builtin_cache()
        with StubSqlAlchemy() as sa:
            with self.assertRaises(ValueError):
                mysql_hardening.get_mysql_engine("mysql")
            mysql_hardening.get_mysql_engine("college_3")
            self.assertEqual(len(sa.created), 1)


# ---------------------------------------------------------------------------
# least-privilege grants + CLI
# ---------------------------------------------------------------------------

class GrantSqlTests(EnvCase):
    def test_select_only_for_exactly_the_allow_list(self):
        sql = mysql_hardening.build_grant_sql({"college_3", "car_1"})
        grants = [l for l in sql.splitlines() if l.startswith("GRANT")]
        self.assertEqual(
            sorted(grants),
            [
                # "_" is a one-character wildcard in GRANT patterns, so it is escaped:
                # an unescaped `car_1` would also match a database called `carX1`.
                "GRANT SELECT ON `car\\_1`.* TO 'qc_readonly'@'%';",
                "GRANT SELECT ON `college\\_3`.* TO 'qc_readonly'@'%';",
            ],
        )
        self.assertNotIn("*.*", sql)
        # Every executable (non-comment) statement must be one of four exact
        # shapes: no FILE / ALL / global / WITH GRANT OPTION grant can slip in.
        statements = [l for l in sql.splitlines() if l and not l.startswith("--")]
        for statement in statements:
            self.assertRegex(
                statement,
                r"^(CREATE USER IF NOT EXISTS '[A-Za-z0-9_]+'@'[^']+' IDENTIFIED BY 'CHANGE_ME' ACCOUNT LOCK;"
                r"|REVOKE ALL PRIVILEGES, GRANT OPTION FROM '[A-Za-z0-9_]+'@'[^']+';"
                r"|GRANT SELECT ON `[a-z0-9\\_]+`\.\* TO '[A-Za-z0-9_]+'@'[^']+';"
                r"|FLUSH PRIVILEGES;)$",
            )
        self.assertIn("REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'qc_readonly'@'%'", sql)
        self.assertIn("CHANGE_ME", sql)
        # created LOCKED, so forgetting to set a real password cannot leave a
        # usable account with a known password
        self.assertIn("IDENTIFIED BY 'CHANGE_ME' ACCOUNT LOCK;", sql)

    def test_untrusted_names_cannot_inject_sql(self):
        sql = mysql_hardening.build_grant_sql(
            {"college_3", "x`; DROP DATABASE college_3; --", "mysql", "sys", "A B"}
        )
        self.assertNotIn("DROP", sql)
        self.assertEqual(
            [l for l in sql.splitlines() if l.startswith("GRANT")],
            ["GRANT SELECT ON `college\\_3`.* TO 'qc_readonly'@'%';"],
        )
        # reserved schemas are named; untrusted names are only counted
        self.assertIn("-- skipped reserved system schema: mysql", sql)
        self.assertIn("-- skipped reserved system schema: sys", sql)
        self.assertIn("-- skipped 2 name(s) that are not valid database names", sql)
        self.assertNotIn("college_3; DROP", sql)

    def test_invalid_account_values_are_rejected(self):
        for kwargs in ({"user": "x'; DROP USER 'root"}, {"host": "%'; --"},
                       {"user": ""}, {"password_placeholder": "a'b"}):
            with self.subTest(kwargs):
                with self.assertRaises(ValueError):
                    mysql_hardening.build_grant_sql({"college_3"}, **kwargs)


class CliTests(EnvCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = hardening_tools.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_mysql_grants_prints_sql(self):
        os.environ["QC_BUILTIN_DATABASES"] = "college_3"
        db_registry.invalidate_builtin_cache()
        code, out, _ = self.run_cli("mysql-grants", "--user", "ro_user", "--host", "10.0.0.%")
        self.assertEqual(code, 0)
        self.assertIn("GRANT SELECT ON `college\\_3`.* TO 'ro_user'@'10.0.0.%';", out)

    def test_mysql_grants_rejects_bad_user(self):
        os.environ["QC_BUILTIN_DATABASES"] = "college_3"
        db_registry.invalidate_builtin_cache()
        code, _, err = self.run_cli("mysql-grants", "--user", "bad user")
        self.assertEqual(code, 2)
        self.assertIn("Invalid MySQL user", err)

    def test_check_reports_findings(self):
        os.environ["QC_BUILTIN_DATABASES"] = "college_3"
        os.environ["MYSQL_PASSWORD"] = FAKE_PASSWORD
        db_registry.invalidate_builtin_cache()
        code, out, _ = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn("root", out)


def tearDownModule():
    import shutil
    shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
