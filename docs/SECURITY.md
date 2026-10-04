# Security notes (Item 1)

This document is the audit trail for the credential / secret clean-up. It states what is
**enforced by code**, what is **only configured**, and what could **not be verified**.

## 1. Credential policy

* No credential, API key or token is stored in the repository. Everything comes from the
  environment (`app/.env`, git-ignored, or your platform's secret store).
* `python -m app.hardening_tools scan-secrets` scans the working tree (values are never
  printed). The test suite runs the same scan (`NoHardcodedCredentialsTests`).
* Test credentials are always obviously fake (`FAKE-...`); the scanner accepts those and
  nothing else that looks like a password.

## 2. Which MySQL account is used, and when

| Component | Account | Fallback |
|---|---|---|
| Web application (`mysql_hardening.mysql_credentials`) | `MYSQL_RO_USER` / `MYSQL_RO_PASSWORD` | **Only if `MYSQL_RO_USER` is empty:** `MYSQL_USER` (defaults to `root` when unset) + `MYSQL_PASSWORD`. Warned at every start-up; refused when `QC_REQUIRE_READONLY_MYSQL=true`. |
| Read-only tools (`check_tables`, `schema_reader`, `rag_execute`, `evaluate`, manual `test_*`) | same as the web application | same fallback, with a printed notice |
| Offline admin tools (`load_*`, `fix_*`, `repair_*`) | `MYSQL_ADMIN_USER` / `MYSQL_ADMIN_PASSWORD` | `MYSQL_USER` / `MYSQL_PASSWORD` with a printed notice. An empty password is an **error**, never assumed. |

The legacy fallback is kept so existing setups do not break. It is an administrative account,
so it must not be used in production: set `MYSQL_RO_USER` and `QC_REQUIRE_READONLY_MYSQL=true`.

## 3. The previously committed password: ACTION REQUIRED

`app/load_one_db.py` and `app/test_mysql.py` contained a plaintext MySQL **root** password
(the value is deliberately not repeated here). It has been removed from the working tree.

**That does not remove it from git history.** The archive this was audited from has no `.git`
directory, so history could **not** be inspected. Treat the value as compromised if it was ever a
real password, or if it is reused anywhere:

1. **Rotate it now, outside the repository:**
   `ALTER USER 'root'@'localhost' IDENTIFIED BY '<new password>';` then update `app/.env` and
   every other place the old value was used.
2. Check whether it is in history (replace the placeholder with the old value):
   `git log --all --oneline -S"<old value>"` and
   `git grep -I "<old value>" $(git rev-list --all)`
3. If either command prints anything: **SECRET EXISTS IN CURRENT HISTORY — ROTATION REQUIRED.**
   Rewriting history (`git filter-repo --replace-text`, or BFG) and force-pushing can reduce
   exposure, but forks, clones, pull-request refs and hosting-provider caches keep their copies.
   **Rotation is the only fix; history rewriting is hygiene.**

## 4. Audit of every MySQL credential / root usage

Classes: **A** production runtime · **B** offline/admin utility · **C** test-only/manual ·
**D** documentation/example · **E** dead/legacy.

| File | Behaviour found | Class | Level (before) | Action taken |
|---|---|---|---|---|
| `app/load_one_db.py` | hard-coded root password literal, root URL | B | **CRITICAL** | literal removed; `admin_settings()` |
| `app/test_mysql.py` | hard-coded root password literal, root URL | C | **CRITICAL** | literal removed; `read_only_settings()`, prints no secret |
| `app/load_spiderman_mysql.py` | `MYSQL_USER` default `root`; `MYSQL_PASSWORD` default `""` (silent empty password); root URL | B | HIGH | `admin_settings()`; empty password rejected; prints `Password: [loaded]` |
| `app/repair_spiderman_partial.py` | root account hard-coded in `pymysql.connect` and in the URL; `local_infile` enabled | B | HIGH | admin helper; `local_infile` kept (the bulk loader needs it), commented as offline-only; printed errors redacted |
| `app/fix_car_1.py`, `app/fix_battle_death.py` | `MYSQL_USER` default `root` | B | MEDIUM | `admin_settings()` |
| `app/check_tables.py` | root account, host and port all hard-coded | B | MEDIUM | `read_only_settings()` |
| `app/schema_reader.py`, `app/rag_execute.py` | root URL with env password | B | MEDIUM | `read_only_settings()` |
| `app/evaluate.py` | root URL with env password | B | MEDIUM | `read_only_settings()`; clear error when unset |
| `app/test_sql_execution.py`, `app/test_clarification_loop.py` | root URL with env password (manual scripts, not run by `unittest`) | C | MEDIUM | `read_only_settings()` |
| `app/queryclarify.py` | credentials via `mysql_hardening`; raw `str(e)` reached clients and the LLM prompt | A | MEDIUM | all exception text redacted |
| `app/mysql_hardening.py` | legacy fallback account named `root` by default | A | MEDIUM | documented, marked, warned at start-up, refusable (strict mode), grants audited |
| `app/admin_credentials.py` (new) | legacy fallback for admin tools | B | LOW | notice printed, never the password |
| `backend/**` | no credential reads (enforced by `test_api_layer_never_reads_credential_environment_variables`) | A | SAFE | none |
| `backend/core/config.py` | reads `MYSQL_HOST`/`MYSQL_PORT` only; **imported nowhere** | E | SAFE | none (dead code, left in place) |
| `app/switch_to_groq.py` | one-shot migration that rewrites `queryclarify.py`/`evaluate.py`; no credentials; its search strings no longer match | E | LOW | none; do not run |
| `.env.example` | empty values; a live `MYSQL_USER=root` line | D | LOW | commented out, with an explanation |
| `README.md` | variable names only | D | SAFE | updated |
| `tests/**` | fake values | C | SAFE | `FAKE-` prefix, scanner-enforced |

No `.env` file or other secret-bearing file exists in the audited archive.

## 5. Where secrets could leak, and what stops it

| Surface | Control | Test |
|---|---|---|
| Source / docs / examples | scanner; `.gitignore` | `test_repository_has_no_hardcoded_credentials` |
| API error bodies | `redact()` at every handler | `test_no_raw_exception_text_reaches_clients_or_prompts` |
| `execution_error`, `validation_message` (API **and** LLM prompt) | `redact()` where the text is created | same |
| Log records and tracebacks | process-wide log-record redaction | `test_log_records_and_tracebacks_are_redacted_globally` |
| `print()` output | exception text redacted before printing | static test above |
| `GET /api/databases` | whitelisted fields; generic error text | `test_database_list_response_never_contains_credentials`, `..._driver_errors_...` |
| Upload metadata | `DatabaseRecord.public()` whitelist | `test_upload_metadata_exposes_only_whitelisted_fields` |
| Engine URL / `repr` | built with `URL.create`; password masked | `test_engine_creation_never_logs_or_exposes_the_password` |
| Generated grant SQL | placeholder only; refuses to embed a configured secret; account created locked | `test_grant_sql_*` |
| Frontend | reads only `VITE_API_URL`; no credential fields | `test_frontend_*` |

Redaction masks: the value of every environment variable whose name contains PASSWORD, PASSWD,
SECRET, TOKEN, API_KEY, PRIVATE_KEY or SALT (plain and URL-encoded), credentials inside URLs, and
`password=...`-style pairs. It cannot mask a secret it does not know about (for example a password
typed into a SQL statement by a user).

## 6. MySQL least privilege: enforced vs only configured

**Enforced by code:** the allow-list; system schemas always refused; `READ ONLY` session;
server-side statement timeout; server-side row cap; socket timeouts; the SQL guardrail; strict
mode (opt-in).

**Verified at run time:** `mysql_hardening.privilege_findings()` reads `SHOW GRANTS` for the
account in use and reports excess privileges (ALL, FILE, INSERT/UPDATE/DELETE/DDL, GRANT OPTION),
global `SELECT`, `SELECT` on system schemas, role grants it cannot inspect, and `SELECT` on
databases outside the allow-list. It runs at start-up and with `check --connect`. An account that
cannot be verified is reported as a **finding**, not a pass.

**Not guaranteed:** the session's `READ ONLY` setting is a convenience, not a security boundary
(the account's grants are). Setting `MYSQL_RO_USER` does not make an account least-privilege:
only the grant audit says whether it is.

## 7. MySQL result buffering

* **Behaviour:** the MySQL driver (`pymysql`'s default cursor) reads the *entire* result set into
  memory inside `execute()`. `fetchmany(MAX_RESULT_ROWS)` only limits what the application keeps.
* **Real risk:** yes. Measured: about **423 bytes per 6-column row, ~400 MB per million rows**, all
  buffered to be discarded. A cross join of two ~2,000-row tables is 4 million rows, easily
  produced inside the 10 s timeout, on a host that may have 512 MB.
* **Mitigation implemented (smallest safe change):** every connection sets
  `sql_select_limit` to the application cap + 1, so a SELECT *without its own LIMIT* (which
  includes an accidental cross join) is stopped by the server at 1001 rows. An explicit `LIMIT`
  in the SQL **overrides** this setting, so it is a mitigation, not a guarantee.
* **Not implemented (P1):** true streaming (`execution_options(stream_results=True)`, which makes
  SQLAlchemy use pymysql's `SSCursor`). It changes the execution path of every built-in database,
  closing a partly-read streamed cursor drains the remaining rows over the network, and it could
  not be validated against a real MySQL server in this environment. It should be done and tested
  against the real server as its own change.

## 8. Cartesian joins

A cartesian join is syntactically valid, read-only SQL; whether it is a mistake is a
**semantic/resource** question that a regex validator cannot answer reliably, so none is
attempted. The controls that bound it:

* MySQL: server-side statement timeout (default 10 s), 30 s socket read timeout, server-side row
  cap, application row cap (1000).
* SQLite uploads: 10 s wall-clock limit (progress handler), 1000-row cap, value-size limit.
* Dangerous SQL stays blocked regardless (see `tests/test_mysql_hardening.py`).

## 9. Remaining risks and what could not be verified

* **The committed password may still be in git history** (not inspectable from the archive). Rotate.
* The legacy `root` fallback still works by default (compatibility). Use `MYSQL_RO_USER` and
  `QC_REQUIRE_READONLY_MYSQL=true` in production.
* Nothing here was run against a real MySQL server, SQLAlchemy, ChromaDB or Gemini; those code
  paths are covered by fakes. `ACCOUNT LOCK` in the generated SQL needs MySQL 5.7.6+ / MariaDB 10.4.2+.
* `frontend/dist` was not built here, so the bundle check is skipped; the frontend *source* is checked.
* ESLint and the real TypeScript build were not run (no `node_modules` offline).
* No authentication, no rate limiting (out of scope here).
* Secrets live in a plain `.env` file; there is no secret-manager integration.
* Uploaded sample values are sent to the LLM provider on every query, and to LangSmith if tracing is enabled.
* The SQL guardrail still rejects some legitimate queries (decimal literals such as `1.5`, strings
  containing `x.y`), because its cross-database check reads string contents.
