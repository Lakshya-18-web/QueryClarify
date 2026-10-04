"""
Security / hygiene regression tests for Item 1.

  1. no hard-coded credentials in the repository
  2. database passwords are not returned by the database-list endpoint
  3. database passwords are not logged
  4. generated grant SQL contains no password
  5. the frontend receives no database credentials
  6. the root-account fallback produces the intended warning
  7. the read-only MySQL configuration stays functional
  8. the SQLite authorizer stays functional
  9. SQLite ATTACH stays blocked at the engine level
 10. dangerous SQL stays blocked
 11. legitimate SQL is unchanged

Everything here is offline: no MySQL, SQLAlchemy or LangChain. Every credential
is obviously fake (FAKE-...).   python -m unittest discover -s tests -v
"""

import contextlib
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="qc_hygiene_")
os.environ.setdefault("QC_DATA_DIR", _TMP)

try:
    from helpers import (  # discovered with -s tests
        FAKE_ADMIN_PASSWORD, FAKE_API_KEY, FAKE_PASSWORD, FAKE_RO_PASSWORD,
        CaptureLogs, FakeDbapiConnection, FakeEngine, StubSqlAlchemy, env,
    )
except ImportError:  # imported as tests.test_secret_hygiene
    from tests.helpers import (
        FAKE_ADMIN_PASSWORD, FAKE_API_KEY, FAKE_PASSWORD, FAKE_RO_PASSWORD,
        CaptureLogs, FakeDbapiConnection, FakeEngine, StubSqlAlchemy, env,
    )

from app import (  # noqa: E402
    admin_credentials, db_engines, db_registry, hardening_tools, mysql_hardening,
    secret_scan, secrets_redaction,
)
from app.guardrails import validate_sql_guardrail  # noqa: E402
from backend.services import database_service  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Variables the tests touch; cleared around every test for isolation.
ALL_ENV = [
    "QC_BUILTIN_DATABASES", "QC_BUILTIN_DATABASES_FILE", "MYSQL_RO_USER",
    "MYSQL_RO_PASSWORD", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_ADMIN_USER",
    "MYSQL_ADMIN_PASSWORD", "MYSQL_HOST", "MYSQL_PORT", "GOOGLE_API_KEY",
    "QC_REQUIRE_READONLY_MYSQL", "QC_MYSQL_MAX_SELECT_ROWS", "QC_OWNER_SALT",
]


class Isolated(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ALL_ENV}
        for key in ALL_ENV:
            os.environ.pop(key, None)
        self._provider = db_registry._builtin_provider
        self._enabled = db_registry.BUILTIN_ENABLED
        db_registry.set_builtin_provider(None)
        mysql_hardening.forget_mysql_engines()

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        db_registry.BUILTIN_ENABLED = self._enabled
        db_registry.set_builtin_provider(self._provider)
        secrets_redaction.uninstall_log_redaction()
        mysql_hardening.forget_mysql_engines()


def allow(*names):
    os.environ["QC_BUILTIN_DATABASES"] = ",".join(names)
    db_registry.invalidate_builtin_cache()


# ===========================================================================
# 1. no hard-coded credentials
# ===========================================================================

class NoHardcodedCredentialsTests(Isolated):
    def test_repository_has_no_hardcoded_credentials(self):
        findings = secret_scan.scan_repository(ROOT)
        self.assertEqual(
            findings, [],
            "hard-coded credentials found:\n" + "\n".join(f.render() for f in findings),
        )

    def test_no_script_builds_a_mysql_url_string(self):
        # URLs must come from URL.create() in the two credential helpers only.
        allowed = {"app/mysql_hardening.py", "app/secret_scan.py", "app/secrets_redaction.py"}
        offenders = []
        for path in list((ROOT / "app").glob("*.py")) + list((ROOT / "backend").rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel in allowed:
                continue
            if re.search(r"mysql\+pymysql://", path.read_text(encoding="utf-8")):
                offenders.append(rel)
        self.assertEqual(offenders, [])

    def test_every_script_that_connects_uses_the_credential_helpers(self):
        skip = {"db_engines.py", "mysql_hardening.py", "admin_credentials.py",
                "fk_reflection.py", "secret_scan.py"}
        offenders = []
        for path in (ROOT / "app").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if path.name in skip or not re.search(r"create_engine\(|pymysql\.connect\(", text):
                continue
            if "app.admin_credentials" not in text and "app.mysql_hardening" not in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_no_secret_default_in_getenv(self):
        pattern = re.compile(
            r"""getenv\(\s*["'][A-Z_]*(PASSWORD|SECRET|TOKEN|API_KEY)[A-Z_]*["']\s*,\s*["'][^"']+["']"""
        )
        hits = [
            p.relative_to(ROOT).as_posix()
            for p in list((ROOT / "app").glob("*.py")) + list((ROOT / "backend").rglob("*.py"))
            if p.name != "secret_scan.py"          # documents the rule it enforces
            and pattern.search(p.read_text(encoding="utf-8"))
        ]
        self.assertEqual(hits, [])

    # --- the scanner itself must actually work (it once missed a plain word) ---

    def scan(self, text, path="app/x.py"):
        return secret_scan.scan_text(path, text)

    def test_scanner_detects_planted_secrets(self):
        word = "pass" + "word"
        cases = {
            "credential-literal": f'{word} = "sunflower9"',
            "getenv-default": 'os.getenv("MYSQL_' + word.upper() + '", "sunflower9")',
            "url-credentials": 'u = "mysql+pymysql' + "://" + 'app:' + "sunflower9" + '@db/x"',
            "root-account": 'u = f"mysql+pymysql' + "://" + "ro" + 'ot:{p}@localhost"',
            "api-key": 'k = "' + "AIza" + "Sy" + "A" * 30 + '"',
        }
        for rule, line in cases.items():
            with self.subTest(rule):
                self.assertIn(rule, {f.rule for f in self.scan(line)}, line)

    def test_scanner_detects_pem_private_key(self):
        header = "-----BEGIN " + "RSA PRIVATE KEY-----"
        self.assertEqual([f.rule for f in self.scan(header)], ["api-key"])

    def test_scanner_flags_plain_word_passwords(self):
        # Regression: a case-insensitive "looks like an env var name" rule once
        # let any plain word through.
        word = "pass" + "word"
        for value in ("hunter2", "sunflower", "letmein", "rootpw", "Passw0rd!"):
            with self.subTest(value):
                self.assertTrue(self.scan(f'{word} = "{value}"'))

    def test_scanner_accepts_placeholders_and_fakes(self):
        word = "pass" + "word"
        for value in ("FAKE-test-password-not-real", "CHANGE_ME", "change-me", "<password>",
                      "${DB_PASSWORD}", "***", "MYSQL_PASSWORD", "your-password-here"):
            with self.subTest(value):
                self.assertEqual(self.scan(f'{word} = "{value}"'), [])

    def test_scanner_honours_the_allow_marker(self):
        word = "pass" + "word"
        self.assertEqual(self.scan(f'{word} = "sunflower9"  # noqa: secret'), [])

    def test_scanner_never_prints_the_secret_value(self):
        word = "pass" + "word"
        planted = "sunflower-9-xyz"
        findings = self.scan(f'{word} = "{planted}"') + self.scan(
            'u = "mysql+pymysql' + "://" + "app:" + planted + '@db/x"'
        )
        self.assertEqual(len(findings), 2)            # both were detected...
        for finding in findings:
            self.assertNotIn(planted, finding.render())   # ...and neither prints the value

    def test_root_rule_is_not_applied_to_tests_but_secret_rule_is(self):
        root_line = 'MYSQL_USER = "ro' + 'ot"'
        self.assertTrue(self.scan(root_line, "app/x.py"))
        self.assertEqual(self.scan(root_line, "tests/x.py"), [])
        word = "pass" + "word"
        self.assertTrue(self.scan(f'{word} = "sunflower9"', "tests/x.py"))

    def test_scan_cli_exit_codes(self):
        clean = Path(tempfile.mkdtemp(dir=_TMP))
        (clean / "a.py").write_text("x = 1\n")
        dirty = Path(tempfile.mkdtemp(dir=_TMP))
        (dirty / "a.py").write_text('pass' + 'word = "sunflower9"\n')

        def run(root):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = hardening_tools.main(["scan-secrets", "--root", str(root)])
            return code, out.getvalue()

        self.assertEqual(run(clean)[0], 0)
        code, output = run(dirty)
        self.assertEqual(code, 1)
        self.assertNotIn("sunflower9", output)
        self.assertIn("ROTATE", output)


# ===========================================================================
# git hygiene
# ===========================================================================

def _git_available():
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


class GitHygieneTests(Isolated):
    REQUIRED_PATTERNS = [
        ".env", ".env.*", "!.env.example", "*.pem", "*.key", "secrets/",
        "grants*.sql", "*.sqlite", "*.sqlite3", "*.scratch", "*.building",
        "data/user_dbs/", "data/registry.db*", "data/chroma/", "data/spiderman/",
        "*.log", ".venv/", "__pycache__/", "node_modules/", "dist/",
    ]

    def test_gitignore_has_the_required_patterns(self):
        lines = {l.strip() for l in (ROOT / ".gitignore").read_text().splitlines()}
        missing = [p for p in self.REQUIRED_PATTERNS if p not in lines]
        self.assertEqual(missing, [])

    def test_allow_list_file_is_not_ignored(self):
        # It must stay committable: it is the reviewable allow-list.
        lines = {l.strip() for l in (ROOT / ".gitignore").read_text().splitlines()}
        self.assertFalse(
            {"data/builtin_databases.txt", "data/", "*.txt"} & lines,
        )

    @unittest.skipUnless(_git_available(), "git is not installed")
    def test_real_git_ignores_secrets_but_not_source(self):
        repo = Path(tempfile.mkdtemp(dir=_TMP))
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        shutil.copy(ROOT / ".gitignore", repo / ".gitignore")

        def ignored(rel):
            return subprocess.run(
                ["git", "-C", str(repo), "check-ignore", "-q", rel]
            ).returncode == 0

        for rel in (".env", "app/.env", ".env.production", "server.pem", "tls/private.key",
                    "grants.sql", "grants-prod.sql", "secrets/token.txt", "credentials.json",
                    "data/user_dbs/u_0123456789ab.sqlite", "data/registry.db",
                    "data/registry.db-journal", "data/chroma/chroma.sqlite3", "x.sqlite",
                    "x.scratch", "x.building", "logs/app.log", "frontend/dist/index.html",
                    "frontend/node_modules/a/b.js", ".venv/bin/python"):
            with self.subTest(rel):
                self.assertTrue(ignored(rel), f"{rel} should be git-ignored")

        for rel in (".env.example", "data/builtin_databases.txt", "app/db_registry.py",
                    "docs/SECURITY.md", "frontend/src/App.tsx", "tests/helpers.py",
                    "README.md"):
            with self.subTest(rel):
                self.assertFalse(ignored(rel), f"{rel} must NOT be git-ignored")

    @unittest.skipUnless(_git_available(), "git is not installed")
    def test_tracked_secret_files_are_reported(self):
        repo = Path(tempfile.mkdtemp(dir=_TMP))
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        for name in (".env", "grants.sql", "server.pem", ".env.example", "ok.py"):
            (repo / name).write_text("x\n")
        subprocess.run(["git", "-C", str(repo), "add", "-f", "."], check=True)
        flagged = {f.path for f in secret_scan.tracked_secret_files(repo)}
        self.assertEqual(flagged, {".env", "grants.sql", "server.pem"})

    def test_tracked_file_check_is_skipped_without_git_history(self):
        self.assertEqual(secret_scan.tracked_secret_files(Path(tempfile.mkdtemp(dir=_TMP))), [])


# ===========================================================================
# redaction (used by logs, API errors and LLM prompts)
# ===========================================================================

class RedactionTests(Isolated):
    def test_url_credentials_are_masked(self):
        url = "mysql+pymysql" + "://" + "app:" + FAKE_PASSWORD + "@db.internal:3306/x"
        out = secrets_redaction.redact(f"could not connect to {url}")
        self.assertNotIn(FAKE_PASSWORD, out)
        self.assertIn("app:***@db.internal", out)

    def test_key_value_pairs_are_masked(self):
        for text in (f"password={FAKE_PASSWORD}", f"api_key: {FAKE_API_KEY}",
                     f"token = '{FAKE_API_KEY}'", f'secret="{FAKE_PASSWORD}"'):
            with self.subTest(text):
                out = secrets_redaction.redact(text)
                self.assertNotIn(FAKE_PASSWORD, out)
                self.assertNotIn(FAKE_API_KEY, out)
                self.assertIn("***", out)

    def test_configured_secret_values_are_masked_even_without_a_label(self):
        with env(MYSQL_RO_PASSWORD=FAKE_RO_PASSWORD, GOOGLE_API_KEY=FAKE_API_KEY):
            out = secrets_redaction.redact(
                f"boom {FAKE_RO_PASSWORD} and {FAKE_API_KEY} and "
                f"{FAKE_RO_PASSWORD.replace('@', '%40').replace('/', '%2F')}"
            )
        self.assertNotIn(FAKE_RO_PASSWORD, out)
        self.assertNotIn(FAKE_API_KEY, out)
        self.assertNotIn("%40", out)

    def test_ordinary_messages_are_untouched(self):
        message = "Unknown column 'Fname' in 'field list' (Student)"
        with env(MYSQL_PASSWORD=FAKE_PASSWORD):
            self.assertEqual(secrets_redaction.redact(message), message)

    def test_very_short_secret_values_are_not_used_as_patterns(self):
        too_short = "a" + "b"                 # 2 characters: would mangle ordinary words
        with env(MYSQL_PASSWORD=too_short):
            self.assertEqual(secrets_redaction.redact("table abc"), "table abc")

    def test_log_records_and_tracebacks_are_redacted_globally(self):
        secrets_redaction.install_log_redaction()
        logger = logging.getLogger("queryclarify.test.redaction")
        with env(MYSQL_RO_PASSWORD=FAKE_RO_PASSWORD), CaptureLogs() as logs:
            logger.warning("connecting with %s", FAKE_RO_PASSWORD)
            logger.error(f"url=mysql+pymysql://u:{FAKE_RO_PASSWORD}@h/db")
            try:
                raise RuntimeError(f"Access denied (password: {FAKE_RO_PASSWORD})")
            except RuntimeError:
                logger.exception("query failed")
        self.assertNotIn(FAKE_RO_PASSWORD, logs.text)
        self.assertIn("Traceback", logs.text)            # the traceback is still useful
        self.assertIn("***", logs.text)

    def test_install_is_idempotent_and_reversible(self):
        original = logging.getLogRecordFactory()
        secrets_redaction.install_log_redaction()
        installed = logging.getLogRecordFactory()
        secrets_redaction.install_log_redaction()
        self.assertIs(logging.getLogRecordFactory(), installed)
        secrets_redaction.uninstall_log_redaction()
        self.assertIs(logging.getLogRecordFactory(), original)


# ===========================================================================
# 2, 3, 4, 5. credentials never leave the process
# ===========================================================================

class NoCredentialLeakTests(Isolated):
    def secrets_env(self):
        return env(MYSQL_RO_USER="qc_readonly", MYSQL_RO_PASSWORD=FAKE_RO_PASSWORD,
                   MYSQL_PASSWORD=FAKE_PASSWORD, MYSQL_ADMIN_PASSWORD=FAKE_ADMIN_PASSWORD,
                   GOOGLE_API_KEY=FAKE_API_KEY)

    ALL_FAKES = (FAKE_RO_PASSWORD, FAKE_PASSWORD, FAKE_ADMIN_PASSWORD, FAKE_API_KEY)

    def assertNoSecrets(self, value, strict=True):
        """No secret VALUE anywhere. ``strict`` (API responses) additionally forbids
        credential-ish words and connection strings: those bodies must be data only.
        Logs and warnings may legitimately mention a variable NAME or "password: ***"."""
        text = json.dumps(value, default=str) if not isinstance(value, str) else value
        for secret in self.ALL_FAKES:
            self.assertNotIn(secret, text)
        if strict:
            self.assertNotRegex(text, r"(?i)password|passwd|connection_string|mysql\+pymysql://")

    # --- 2. database-list endpoint -------------------------------------------------

    def test_database_list_response_never_contains_credentials(self):
        allow("college_3", "car_1")
        engine = FakeEngine({"SHOW DATABASES": [("college_3",), ("mysql",), ("information_schema",)]})
        with self.secrets_env(), StubSqlAlchemy():
            response = database_service.list_databases_response(
                db_registry.hash_owner("a" * 32), engine_for=lambda name: engine
            )
        self.assertNoSecrets(response)
        self.assertEqual(response["databases"], ["college_3"])      # allow-list ∩ visible
        self.assertEqual(set(response),
                         {"count", "databases", "user_databases", "builtin_enabled", "builtin_error"})

    def test_system_schemas_never_appear_in_the_list(self):
        allow("college_3")
        engine = FakeEngine({"SHOW DATABASES": [("mysql",), ("sys",), ("performance_schema",),
                                                ("information_schema",), ("college_3",)]})
        with StubSqlAlchemy():
            response = database_service.list_databases_response("", engine_for=lambda n: engine)
        self.assertEqual(response["databases"], ["college_3"])

    def test_driver_errors_do_not_reach_the_list_response_or_logs(self):
        allow("college_3")
        secrets_redaction.install_log_redaction()
        leaky = RuntimeError(
            "(2003, \"Can't connect\") access denied (using password: YES) "
            + "mysql+pymysql" + "://" + "qc_readonly:" + FAKE_RO_PASSWORD + "@db/x "
            + FAKE_RO_PASSWORD
        )
        with self.secrets_env(), StubSqlAlchemy(), CaptureLogs() as logs:
            response = database_service.list_databases_response(
                "", engine_for=lambda name: FakeEngine(fail_with=leaky)
            )
        self.assertEqual(response["builtin_error"], "Built-in databases are temporarily unavailable.")
        self.assertNoSecrets(response)
        self.assertNoSecrets(logs.text, strict=False)
        self.assertEqual(response["databases"], ["college_3"])        # still usable

    def test_upload_metadata_exposes_only_whitelisted_fields(self):
        record = db_registry.DatabaseRecord(
            id="u_0123456789ab", display_name="Sales", owner_hash="deadbeef" * 8,
            dialect="sqlite", storage_path="/srv/data/user_dbs/u_0123456789ab.sqlite",
            status="ready", source_type="sqlite", table_count=2, row_count=10,
            size_bytes=2048, created_at="2026-01-01T00:00:00+00:00", expires_at=None,
            error="internal failure detail",
        )
        self.assertEqual(
            set(record.public()),
            {"id", "display_name", "dialect", "status", "source_type", "table_count",
             "row_count", "size_bytes", "created_at", "expires_at"},
        )

    def test_api_layer_never_reads_credential_environment_variables(self):
        offenders = []
        for path in (ROOT / "backend").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            # actual reads of credential variables / use of the credential helpers
            # (a MYSQL_HOST or a mention in a comment is not a credential read)
            if re.search(r"(getenv|environ)[^\n]*MYSQL_(USER|PASSWORD|RO_|ADMIN)", text) \
                    or re.search(r"\b(mysql_credentials|admin_settings|read_only_settings)\s*\(", text) \
                    or re.search(r"(getenv|environ)[^\n]*(PASSWORD|SECRET|TOKEN|API_KEY)", text):
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(offenders, [])

    def test_no_raw_exception_text_reaches_clients_or_prompts(self):
        # Every str(e)/str(exc)/str(error) in the serving path must go through redact().
        files = [ROOT / "app" / "queryclarify.py"] + list((ROOT / "backend").rglob("*.py"))
        bad = []
        for path in files:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"str\((e|exc|error)\)|detail\s*=\s*str\(", line) or \
                   re.search(r"\{(e|exc|error)\}", line):
                    bad.append(f"{path.relative_to(ROOT).as_posix()}:{number}")
        self.assertEqual(bad, [])

    # --- 3. start-up diagnostics and engine setup are not logged with passwords -----

    def test_startup_warnings_never_contain_passwords(self):
        allow("college_3")
        with self.secrets_env():
            findings = mysql_hardening.security_warnings()
            findings += mysql_hardening.privilege_findings(
                FakeEngine(fail_with=RuntimeError("denied " + FAKE_RO_PASSWORD))
            )
        self.assertTrue(findings)
        self.assertNoSecrets(findings, strict=False)

    def test_engine_creation_never_logs_or_exposes_the_password(self):
        allow("college_3")
        secrets_redaction.install_log_redaction()
        with self.secrets_env(), StubSqlAlchemy() as sa, CaptureLogs() as logs:
            engine = mysql_hardening.get_mysql_engine("college_3")
            sa.listeners[0][2](FakeDbapiConnection(fail_on=["SET SESSION"]), None)
        self.assertNotIn(FAKE_RO_PASSWORD, logs.text)
        self.assertNotIn(FAKE_RO_PASSWORD, str(engine.url))
        self.assertNotIn(FAKE_RO_PASSWORD, repr(engine.url))

    def test_connection_settings_object_hides_its_password(self):
        with env(MYSQL_ADMIN_USER="admin_user", MYSQL_ADMIN_PASSWORD=FAKE_ADMIN_PASSWORD):
            settings = admin_credentials.admin_settings()
        self.assertNotIn(FAKE_ADMIN_PASSWORD, repr(settings))
        self.assertNotIn(FAKE_ADMIN_PASSWORD, str(settings))

    # --- 4. generated grant SQL ---------------------------------------------------

    def test_grant_sql_contains_no_password(self):
        with self.secrets_env():
            sql = mysql_hardening.build_grant_sql({"college_3", "car_1"})
        for secret in self.ALL_FAKES:
            self.assertNotIn(secret, sql)
        self.assertIn("CHANGE_ME", sql)

    def test_grant_cli_output_contains_no_password(self):
        allow("college_3")
        out = io.StringIO()
        with self.secrets_env(), contextlib.redirect_stdout(out):
            self.assertEqual(hardening_tools.main(["mysql-grants"]), 0)
        for secret in self.ALL_FAKES:
            self.assertNotIn(secret, out.getvalue())

    def test_grant_sql_refuses_to_embed_a_configured_secret(self):
        with self.secrets_env():
            with self.assertRaises(ValueError):
                mysql_hardening.build_grant_sql({"college_3"}, password_placeholder=FAKE_RO_PASSWORD)

    # --- 5. frontend ----------------------------------------------------------------

    def test_frontend_reads_only_the_api_url_from_the_environment(self):
        names = set()
        for path in (ROOT / "frontend" / "src").rglob("*"):
            if path.suffix in {".ts", ".tsx"}:
                names |= set(re.findall(r"import\.meta\.env\??\.(\w+)", path.read_text(encoding="utf-8")))
        self.assertEqual(names, {"VITE_API_URL"})

    def test_frontend_has_no_credential_fields_or_secret_env_names(self):
        sources = {p: p.read_text(encoding="utf-8")
                   for p in (ROOT / "frontend").rglob("*")
                   if p.suffix in {".ts", ".tsx", ".html", ".json", ".css"}
                   and "node_modules" not in p.parts and p.name != "package-lock.json"}
        self.assertTrue(sources)
        for path, text in sources.items():
            with self.subTest(path.name):
                self.assertNotRegex(text, r'type=["\']password["\']')
                self.assertNotRegex(text, r"VITE_\w*(PASSWORD|SECRET|TOKEN|KEY)")

    def test_frontend_database_type_has_no_credential_fields(self):
        text = (ROOT / "frontend" / "src" / "api.ts").read_text(encoding="utf-8")
        block = re.search(r"export type UserDatabase = \{(.*?)\};", text, re.S).group(1)
        fields = set(re.findall(r"^\s*(\w+)\s*:", block, re.M))
        self.assertFalse({f for f in fields if re.search(r"(?i)pass|secret|token|conn|host|path|owner", f)})

    @unittest.skipUnless((ROOT / "frontend" / "dist").exists(), "frontend has not been built")
    def test_built_frontend_bundle_contains_no_secrets(self):
        self.assertEqual(secret_scan.scan_repository(ROOT / "frontend" / "dist"), [])


# ===========================================================================
# 6, 7. root fallback, strict mode, privilege audit, read-only config
# ===========================================================================

class RootFallbackTests(Isolated):
    def test_root_fallback_warns_that_the_account_is_administrative(self):
        allow("college_3")
        with env(MYSQL_PASSWORD=FAKE_PASSWORD):               # MYSQL_USER unset -> "root"
            warnings = mysql_hardening.security_warnings()
        joined = " ".join(warnings)
        self.assertIn("ADMINISTRATIVE", joined)
        self.assertIn("'root'", joined)
        self.assertIn("legacy", joined)
        self.assertIn("MYSQL_RO_USER", joined)
        self.assertNotIn(FAKE_PASSWORD, joined)

    def test_explicit_admin_name_in_legacy_user_also_warns(self):
        allow("college_3")
        with env(MYSQL_USER="admin", MYSQL_PASSWORD=FAKE_PASSWORD):
            self.assertIn("ADMINISTRATIVE", " ".join(mysql_hardening.security_warnings()))

    def test_non_admin_legacy_account_still_gets_a_notice(self):
        allow("college_3")
        with env(MYSQL_USER="reporting", MYSQL_PASSWORD=FAKE_PASSWORD):
            warnings = mysql_hardening.security_warnings()
        self.assertEqual(len(warnings), 1)
        self.assertIn("legacy MYSQL_USER", warnings[0])

    def test_read_only_account_produces_no_warning(self):
        allow("college_3")
        with env(MYSQL_RO_USER="qc_readonly", MYSQL_RO_PASSWORD=FAKE_RO_PASSWORD):
            self.assertEqual(mysql_hardening.security_warnings(), [])

    def test_a_read_only_user_that_is_really_root_is_reported(self):
        allow("college_3")
        with env(MYSQL_RO_USER="root", MYSQL_RO_PASSWORD=FAKE_RO_PASSWORD):
            self.assertIn("administrative", " ".join(mysql_hardening.security_warnings()))

    def test_credentials_prefer_the_read_only_account(self):
        with env(MYSQL_USER="root", MYSQL_PASSWORD=FAKE_PASSWORD,
                 MYSQL_RO_USER="qc_readonly", MYSQL_RO_PASSWORD=FAKE_RO_PASSWORD):
            self.assertEqual(mysql_hardening.mysql_credentials(),
                             ("qc_readonly", FAKE_RO_PASSWORD, True))

    # --- 7. read-only configuration stays functional -------------------------------

    def test_read_only_engine_uses_the_ro_account_not_root(self):
        allow("college_3")
        with env(MYSQL_USER="root", MYSQL_PASSWORD=FAKE_PASSWORD,
                 MYSQL_RO_USER="qc_readonly", MYSQL_RO_PASSWORD=FAKE_RO_PASSWORD), \
                StubSqlAlchemy() as sa:
            engine = mysql_hardening.get_mysql_engine("college_3")
        self.assertEqual(sa.url_calls[0]["username"], "qc_readonly")
        self.assertEqual(sa.url_calls[0]["password"], FAKE_RO_PASSWORD)
        self.assertEqual(engine.kwargs["connect_args"], mysql_hardening.connect_args())

    def test_session_is_made_read_only_with_timeout_and_row_cap(self):
        conn = FakeDbapiConnection()
        applied = mysql_hardening.apply_session_limits(conn)
        self.assertEqual(applied, ["read_only", "statement_timeout", "row_cap"])
        statements = " ".join(conn.cur.executed)
        self.assertIn("TRANSACTION READ ONLY", statements)
        self.assertIn("MAX_EXECUTION_TIME", statements)
        self.assertIn("sql_select_limit = 1001", statements)

    def test_row_cap_is_configurable_and_defaults_to_the_app_cap_plus_one(self):
        from app.guardrails import MAX_RESULT_ROWS
        self.assertEqual(mysql_hardening.max_select_rows(), MAX_RESULT_ROWS + 1)
        with env(QC_MYSQL_MAX_SELECT_ROWS="50"):
            self.assertEqual(mysql_hardening.max_select_rows(), 50)

    # --- strict mode ---------------------------------------------------------------

    def test_strict_mode_refuses_the_legacy_account(self):
        allow("college_3")
        with env(QC_REQUIRE_READONLY_MYSQL="true", MYSQL_PASSWORD=FAKE_PASSWORD), \
                StubSqlAlchemy() as sa:
            with self.assertRaises(RuntimeError) as ctx:
                mysql_hardening.get_mysql_engine("college_3")
        self.assertEqual(sa.created, [])
        self.assertNotIn(FAKE_PASSWORD, str(ctx.exception))

    def test_strict_mode_refuses_an_admin_named_read_only_user(self):
        allow("college_3")
        with env(QC_REQUIRE_READONLY_MYSQL="1", MYSQL_RO_USER="root", MYSQL_RO_PASSWORD=FAKE_RO_PASSWORD), \
                StubSqlAlchemy():
            with self.assertRaises(RuntimeError):
                mysql_hardening.get_mysql_engine("college_3")

    def test_strict_mode_allows_a_dedicated_account(self):
        allow("college_3")
        with env(QC_REQUIRE_READONLY_MYSQL="true", MYSQL_RO_USER="qc_readonly",
                 MYSQL_RO_PASSWORD=FAKE_RO_PASSWORD), StubSqlAlchemy() as sa:
            mysql_hardening.get_mysql_engine("college_3")
        self.assertEqual(len(sa.created), 1)

    def test_strict_mode_is_off_by_default(self):
        allow("college_3")
        with env(MYSQL_PASSWORD=FAKE_PASSWORD), StubSqlAlchemy() as sa:
            mysql_hardening.get_mysql_engine("college_3")      # legacy fallback still works
        self.assertEqual(len(sa.created), 1)

    # --- privilege audit: env vars alone prove nothing -----------------------------

    def test_grants_audit_accepts_a_select_only_account(self):
        lines = ["GRANT USAGE ON *.* TO `qc_readonly`@`%`",
                 "GRANT SELECT ON `college\\_3`.* TO `qc_readonly`@`%`"]
        self.assertEqual(mysql_hardening.audit_grants(lines, {"college_3"}), [])

    def test_grants_audit_flags_every_kind_of_excess(self):
        findings = " | ".join(mysql_hardening.audit_grants([
            "GRANT ALL PRIVILEGES ON *.* TO `root`@`localhost` WITH GRANT OPTION",
            "GRANT SELECT, INSERT, FILE ON *.* TO `u`@`%`",
            "GRANT SELECT ON `mysql`.* TO `u`@`%`",
            "GRANT SELECT ON `payroll`.* TO `u`@`%`",
            "GRANT `app_role`@`%` TO `u`@`%`",
        ], {"college_3"}))
        for expected in ("ALL PRIVILEGES", "GRANT OPTION", "FILE", "INSERT",
                         "ALL databases", "system schema `mysql`", "outside the allow-list: payroll",
                         "role grants"):
            self.assertIn(expected, findings)

    def test_grants_audit_never_echoes_raw_lines_or_password_hashes(self):
        hashed = "*" + "0123456789ABCDEF" * 2 + "01234567"
        out = " ".join(mysql_hardening.audit_grants(
            [f"GRANT ALL ON *.* TO 'u'@'%' IDENTIFIED BY PASSWORD '{hashed}'"], set()))
        self.assertNotIn(hashed, out)
        self.assertNotIn("IDENTIFIED", out)

    def test_privilege_findings_reads_real_grants_through_the_engine(self):
        allow("college_3")
        engine = FakeEngine({"SHOW GRANTS": [("GRANT ALL PRIVILEGES ON *.* TO `root`@`%`",)]})
        with StubSqlAlchemy():
            findings = mysql_hardening.privilege_findings(engine)
        self.assertTrue(any("ALL PRIVILEGES" in f for f in findings))
        self.assertEqual(engine.executed, ["SHOW GRANTS FOR CURRENT_USER()"])

    def test_unverifiable_account_is_a_finding_not_a_pass(self):
        allow("college_3")
        engine = FakeEngine(fail_with=RuntimeError("denied " + FAKE_RO_PASSWORD))
        with env(MYSQL_RO_PASSWORD=FAKE_RO_PASSWORD), StubSqlAlchemy():
            findings = mysql_hardening.privilege_findings(engine)
        self.assertEqual(len(findings), 1)
        self.assertIn("could not be verified", findings[0])
        self.assertNotIn(FAKE_RO_PASSWORD, findings[0])

    def test_default_owner_salt_is_reported(self):
        self.assertTrue(db_registry.owner_salt_is_default())


# ===========================================================================
# offline admin tools
# ===========================================================================

class AdminCredentialTests(Isolated):
    def capture_stderr(self):
        return contextlib.redirect_stderr(io.StringIO())

    def test_admin_vars_take_precedence_and_print_no_notice(self):
        err = io.StringIO()
        with env(MYSQL_ADMIN_USER="loader", MYSQL_ADMIN_PASSWORD=FAKE_ADMIN_PASSWORD,
                 MYSQL_USER="root", MYSQL_PASSWORD=FAKE_PASSWORD), contextlib.redirect_stderr(err):
            settings = admin_credentials.admin_settings()
        self.assertEqual((settings.user, settings.password, settings.source),
                         ("loader", FAKE_ADMIN_PASSWORD, "MYSQL_ADMIN_*"))
        self.assertEqual(err.getvalue(), "")

    def test_legacy_fallback_is_announced_without_the_password(self):
        admin_credentials._notice_printed.clear()
        err = io.StringIO()
        with env(MYSQL_PASSWORD=FAKE_PASSWORD), contextlib.redirect_stderr(err):
            settings = admin_credentials.admin_settings()
        self.assertEqual(settings.source, "legacy")
        self.assertIn("legacy MYSQL_USER/MYSQL_PASSWORD", err.getvalue())
        self.assertNotIn(FAKE_PASSWORD, err.getvalue())

    def test_missing_credentials_is_an_error_never_an_empty_password(self):
        with self.assertRaises(admin_credentials.MissingCredentialsError) as ctx:
            admin_credentials.admin_settings()
        self.assertIn("MYSQL_ADMIN_PASSWORD", str(ctx.exception))
        with env(MYSQL_USER="root", MYSQL_PASSWORD=""):
            with self.assertRaises(admin_credentials.MissingCredentialsError):
                admin_credentials.admin_settings()

    def test_read_only_tools_prefer_the_read_only_account(self):
        admin_credentials._notice_printed.clear()
        with env(MYSQL_RO_USER="qc_readonly", MYSQL_RO_PASSWORD=FAKE_RO_PASSWORD,
                 MYSQL_PASSWORD=FAKE_PASSWORD):
            settings = admin_credentials.read_only_settings()
        self.assertEqual((settings.user, settings.source), ("qc_readonly", "MYSQL_RO_*"))

    def test_read_only_tools_without_credentials_fail_clearly(self):
        with self.assertRaises(admin_credentials.MissingCredentialsError):
            admin_credentials.read_only_settings()

    def test_url_and_pymysql_arguments_are_built_from_parts(self):
        with env(MYSQL_ADMIN_USER="loader", MYSQL_ADMIN_PASSWORD=FAKE_ADMIN_PASSWORD,
                 MYSQL_HOST="db.internal", MYSQL_PORT="3307"), StubSqlAlchemy() as sa:
            settings = admin_credentials.admin_settings()
            url = admin_credentials.mysql_url(settings, "car_1")
            kwargs = admin_credentials.pymysql_kwargs(settings, "car_1", autocommit=True)
        self.assertEqual(sa.url_calls[0], dict(drivername="mysql+pymysql", username="loader",
                                               password=FAKE_ADMIN_PASSWORD, host="db.internal",
                                               port=3307, database="car_1"))
        self.assertNotIn(FAKE_ADMIN_PASSWORD, str(url))
        self.assertEqual(kwargs["port"], 3307)
        self.assertTrue(kwargs["autocommit"])

    def test_bad_port_is_reported_without_credentials(self):
        with env(MYSQL_ADMIN_USER="loader", MYSQL_ADMIN_PASSWORD=FAKE_ADMIN_PASSWORD, MYSQL_PORT="x"):
            with self.assertRaises(admin_credentials.MissingCredentialsError) as ctx:
                admin_credentials.admin_settings()
        self.assertNotIn(FAKE_ADMIN_PASSWORD, str(ctx.exception))


# ===========================================================================
# 8, 9, 10, 11. Item 1 behaviour that must not regress
# ===========================================================================

class Item1RegressionPins(Isolated):
    def make_db(self):
        folder = Path(tempfile.mkdtemp(dir=_TMP))
        path = folder / "a.sqlite"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        conn.executemany("INSERT INTO t VALUES (?, ?)", [(1, "a"), (2, "b")])
        conn.commit()
        conn.close()
        return folder, path

    def test_8_sqlite_authorizer_denies_every_write(self):
        _, path = self.make_db()
        before = path.read_bytes()
        for sql in ("INSERT INTO t VALUES (9, 'x')", "UPDATE t SET name = 'x'", "DELETE FROM t",
                    "DROP TABLE t", "CREATE TABLE z (a)", "ALTER TABLE t ADD COLUMN c"):
            with self.subTest(sql), self.assertRaises(sqlite3.DatabaseError):
                db_engines.run_readonly_query(path, sql)
        self.assertEqual(path.read_bytes(), before)             # file untouched
        _, rows, _ = db_engines.run_readonly_query(path, "SELECT * FROM t")
        self.assertEqual(len(rows), 2)

    def test_9_attach_is_blocked_by_the_engine_not_just_the_guardrail(self):
        folder, path = self.make_db()
        other = folder / "secret.sqlite"
        sqlite3.connect(other).execute("CREATE TABLE s (v)").connection.close()
        # Straight to the engine: the guardrail is NOT involved here.
        with self.assertRaises(sqlite3.DatabaseError):
            db_engines.run_readonly_query(path, f"ATTACH DATABASE '{other}' AS x")
        conn = db_engines.open_readonly_connection(path)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute(f"ATTACH DATABASE '{other}' AS x")
            with self.assertRaises(sqlite3.DatabaseError):      # and so is introspecting it
                conn.execute("SELECT * FROM x.s")
        finally:
            conn.close()

    def test_8_authorizer_blocks_pragmas_and_extensions(self):
        _, path = self.make_db()
        for sql in ("PRAGMA query_only = OFF", "PRAGMA writable_schema = ON",
                    "SELECT load_extension('x')", "VACUUM"):
            with self.subTest(sql), self.assertRaises(sqlite3.DatabaseError):
                db_engines.run_readonly_query(path, sql)

    def test_10_dangerous_sql_stays_blocked(self):
        dangerous = [
            "SELECT * FROM Student INTO OUTFILE '/tmp/dump';", "SELECT 'a' INTO DUMPFILE '/tmp/y';",
            "SELECT LOAD_FILE('/etc/passwd');", "LOAD DATA INFILE '/tmp/x' INTO TABLE Student;",
            "SELECT SLEEP(3600);", "SELECT BENCHMARK(1000000000, MD5('a'));",
            "SELECT GET_LOCK('x', 600);", "SELECT RELEASE_LOCK('x');",
            "SELECT * FROM mysql.user;", "SELECT * FROM information_schema.tables;",
            "DROP TABLE Student;", "SELECT 1; DROP TABLE Student;", "SELECT @@datadir;",
            "SELECT `a'` , SLEEP(5) , 'b' FROM Student",
        ]
        for sql in dangerous:
            with self.subTest(sql):
                self.assertFalse(validate_sql_guardrail(sql, "college_3").allowed)
        for system_db in ("mysql", "information_schema", "performance_schema", "sys"):
            self.assertFalse(validate_sql_guardrail("SELECT * FROM user;", system_db).allowed)

    def test_11_legitimate_sql_is_unchanged(self):
        legitimate = [
            "SELECT count(*) FROM Student;",
            "SELECT Fname, LName FROM Student WHERE Age > 20 ORDER BY LName;",
            "SELECT T1.Fname FROM Student AS T1 JOIN Enrolled_in AS T2 ON T1.StuID = T2.StuID;",
            "SELECT city_code, COUNT(*) FROM Student GROUP BY city_code;",
            "WITH c AS (SELECT * FROM Student) SELECT * FROM c;",
            "SELECT * FROM Student WHERE Fname LIKE '%sleep(%';",
        ]
        for sql in legitimate:
            with self.subTest(sql):
                result = validate_sql_guardrail(sql, "college_3")
                self.assertTrue(result.allowed, result.reason)

    def test_builtin_allow_list_and_system_schema_blocking_remain(self):
        allow("college_3")
        self.assertTrue(db_registry.is_builtin_allowed("college_3"))
        for name in ("mysql", "information_schema", "performance_schema", "sys", "other_db"):
            self.assertFalse(db_registry.is_builtin_allowed(name))


class SqliteDefenceLayerTests(Isolated):
    """
    User-database queries are protected by SEVERAL independent layers. The
    behaviour tests above would still pass if any single layer were removed (that
    is the point of defence in depth), so each layer is pinned on its own here.
    """

    def make_db(self):
        path = Path(tempfile.mkdtemp(dir=_TMP)) / "a.sqlite"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'a')")
        conn.commit()
        conn.close()
        return path

    def bare_connection(self):
        """A hardened connection with ONLY the authorizer removed."""
        conn = db_engines.open_readonly_connection(self.make_db())
        conn.set_authorizer(None)
        self.addCleanup(conn.close)
        return conn

    # --- layer 1: the SQLite authorizer (the engine decides, not the SQL text) ---

    def test_authorizer_callback_allows_reads_only(self):
        authorizer = db_engines._make_authorizer()
        ok, deny = sqlite3.SQLITE_OK, sqlite3.SQLITE_DENY

        for name in ("SELECT", "READ", "RECURSIVE"):
            self.assertEqual(authorizer(getattr(sqlite3, f"SQLITE_{name}"), None, None, None, None), ok, name)

        for name in ("INSERT", "UPDATE", "DELETE", "CREATE_TABLE", "DROP_TABLE", "ALTER_TABLE",
                     "CREATE_INDEX", "DROP_INDEX", "CREATE_TRIGGER", "CREATE_VIEW", "REINDEX",
                     "ATTACH", "DETACH"):
            self.assertEqual(authorizer(getattr(sqlite3, f"SQLITE_{name}"), "x", None, None, None),
                             deny, f"{name} must be DENIED by the authorizer itself")

    def test_authorizer_pragma_and_function_whitelists(self):
        authorizer = db_engines._make_authorizer()
        ok, deny = sqlite3.SQLITE_OK, sqlite3.SQLITE_DENY
        for pragma in ("table_info", "table_xinfo", "foreign_key_list", "index_list", "database_list"):
            self.assertEqual(authorizer(sqlite3.SQLITE_PRAGMA, pragma, None, None, None), ok, pragma)
        for pragma in ("query_only", "writable_schema", "trusted_schema", "journal_mode", "foreign_keys"):
            self.assertEqual(authorizer(sqlite3.SQLITE_PRAGMA, pragma, "OFF", None, None), deny, pragma)
        self.assertEqual(authorizer(sqlite3.SQLITE_FUNCTION, None, "load_extension", None, None), deny)
        self.assertEqual(authorizer(sqlite3.SQLITE_FUNCTION, None, "lower", None, None), ok)

    def test_authorizer_is_installed_on_every_connection(self):
        path = self.make_db()
        conn = db_engines.open_readonly_connection(path)
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.DatabaseError):          # denied by the authorizer
            conn.execute("PRAGMA query_only = OFF")

    # --- layer 2: the file is opened read-only (mode=ro) ---------------------------

    def test_file_is_opened_read_only_even_without_authorizer_or_query_only(self):
        conn = self.bare_connection()
        conn.execute("PRAGMA query_only = OFF")                  # layer 3 removed too
        with self.assertRaises(sqlite3.OperationalError) as ctx:
            conn.execute("INSERT INTO t VALUES (2, 'b')")
        self.assertIn("readonly", str(ctx.exception).lower())

    # --- layer 3: PRAGMA query_only ------------------------------------------------

    def test_query_only_pragma_is_enabled(self):
        conn = self.bare_connection()
        self.assertEqual(conn.execute("PRAGMA query_only").fetchone()[0], 1)

    def test_trusted_schema_is_disabled(self):
        conn = self.bare_connection()
        self.assertEqual(conn.execute("PRAGMA trusted_schema").fetchone()[0], 0)

    # --- layer 4: ATTACH is disabled by a hard limit -------------------------------

    @unittest.skipUnless(hasattr(sqlite3.Connection, "getlimit"), "needs Python 3.11+")
    def test_attach_is_disabled_by_the_engine_limit(self):
        conn = self.bare_connection()                            # authorizer removed
        self.assertEqual(conn.getlimit(sqlite3.SQLITE_LIMIT_ATTACHED), 0)
        other = self.make_db()
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute(f"ATTACH DATABASE '{other}' AS x")

    @unittest.skipUnless(hasattr(sqlite3.Connection, "getlimit"), "needs Python 3.11+")
    def test_value_size_limit_is_applied(self):
        conn = self.bare_connection()
        self.assertEqual(conn.getlimit(sqlite3.SQLITE_LIMIT_LENGTH), db_engines.MAX_VALUE_BYTES)


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
