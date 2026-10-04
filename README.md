# QueryClarify

Agentic RAG Text-to-SQL System.

Ask questions in plain English. QueryClarify asks for clarification when the
question is ambiguous, retrieves the relevant schema with RAG, generates and
validates SQL, runs it, and self-corrects on errors.

It works on two kinds of databases:

| | Built-in | Bring your own (new) |
|---|---|---|
| Source | The 157 Spider databases in MySQL | Uploaded by the user |
| Formats | MySQL | `.sqlite` `.sqlite3` `.db`, `.csv` `.tsv` `.xlsx` |
| Who can see it | Everyone | Only the browser that uploaded it |
| Routing | Router picks the database from the question | User selects it explicitly |
| Lifetime | Permanent | Auto-deleted after `QC_USER_DB_TTL_DAYS` (default 7) |

## Bring-your-own-database

Open **My Databases** in the sidebar, drop in a file, and click
**Upload & index**. You get a summary of the tables, row counts and the
relationships that were found, then **Start asking questions**.

What happens on upload:

1. **Validate**: file type is checked by content, not just extension. Size, file count,
   table, column and row limits are enforced.
2. **Sanitize**: the database is *rebuilt*, not used as-is. Only plain tables are
   copied; views, triggers, virtual tables and other executable schema content are
   dropped. Identifiers are normalised to `[A-Za-z0-9_]`.
   CSV/Excel files become one table per file/sheet with inferred types and keys.
3. **Detect relationships**: declared foreign keys are kept. For flat files, likely
   keys are inferred by naming *and* verified against the data (at least 90% of values
   must match). The UI labels these as "detected".
4. **Index**: one schema document per table goes into the existing ChromaDB
   `spiderman_schema` collection, in the format the pipeline already uses,
   tagged with the database id. The router collection is untouched, so user
   databases never leak into built-in routing.
5. **Register**: ownership, size and expiry are recorded in `data/registry.db`.

Any failure rolls everything back (file, index entries, registry row).

### Security model

* **Read-only at the engine level.** User databases are opened `mode=ro` with
  `PRAGMA query_only`, a SQLite *authorizer* that only permits `SELECT`/read
  operations and introspection pragmas (so even if the SQL validator is fooled, SQLite
  itself refuses writes, `ATTACH`, `load_extension`, etc.), attached databases disabled,
  size limits on values, and a wall-clock timeout (`QC_QUERY_TIMEOUT_SECONDS`).
* **Validator hardened** (`app/guardrails.py`) to also block `PRAGMA`, `ATTACH`,
  `DETACH`, `VACUUM`, `LOAD_EXTENSION` (see also *Built-in MySQL hardening*).
* **Owner isolation.** Every database-scoped endpoint (query, tables, schema, delete)
  checks ownership. Someone else's id returns the same 404 as a non-existent one. A user
  id can never fall through to the MySQL path. History is also per-client.
* **Prompt-injection hygiene.** Sample values are flattened to one short line, and prompts
  state that schema contents are data, not instructions.
* **Quotas.** Per-user database count and storage, upload size, rows, tables.

## Built-in MySQL hardening

The 157 built-in databases are protected in layers, so no single bypass is enough:

1. **Allow-list.** Only databases on the allow-list can be queried. An arbitrary
   name, or a system schema (`mysql`, `information_schema`, `performance_schema`,
   `sys`), is refused everywhere: API, query pipeline and engine factory. System
   schemas are rejected even if someone lists them in the allow-list. Source order:
   `QC_BUILTIN_DATABASES` -> `data/builtin_databases.txt` -> the router index
   (ChromaDB `spiderman_databases`). **If none yields a name, built-in databases are
   unavailable (fail closed).**
2. **Least privilege.** Set `MYSQL_RO_USER` / `MYSQL_RO_PASSWORD` to a SELECT-only
   account. The server warns at start-up if it is still running as `root`.
3. **Session limits.** Every connection is `READ ONLY` with a server-side statement
   timeout (`MAX_EXECUTION_TIME`, or `max_statement_time` on MariaDB) plus client
   socket timeouts.
4. **SQL guardrail.** Blocks `INTO` (`OUTFILE`, `DUMPFILE`, `@var`), `LOAD_FILE`,
   `LOAD DATA`, `SLEEP`, `BENCHMARK`, `GET_LOCK`, `RELEASE_LOCK` (and the other lock/wait
   functions), `@`/`@@` variables, `FOR UPDATE`/`FOR SHARE`, `USER()`/`VERSION()`/
   `DATABASE()`, and any reference to a system schema. It tokenises quotes left to right
   (strings, `"..."`, `` `identifiers` ``) so a quote inside one cannot hide code in
   another, rejects backslashes and unterminated quotes, and ignores dangerous words that
   are only data or quoted column names.

One-time setup:

```bash
# 1. record the allow-list (reads your existing router index; commit the file)
python -m app.hardening_tools export-allowlist

# 2. generate the SELECT-only account SQL and run it as a MySQL administrator
python -m app.hardening_tools mysql-grants --user qc_readonly > grants.sql
mysql -u <admin> -p < grants.sql
rm grants.sql
```

The generated SQL creates the account **locked**, with a throw-away placeholder password, so
forgetting the next step cannot leave a usable account with a known password. Set the real
password **interactively in the mysql client** (never inside `grants.sql`, which is git-ignored
for exactly this reason):

```sql
ALTER USER 'qc_readonly'@'%' IDENTIFIED BY '<type it here>' ACCOUNT UNLOCK;
```

```bash
# 3. put MYSQL_RO_USER / MYSQL_RO_PASSWORD in app/.env (or your secret store), then verify
python -m app.hardening_tools check --connect
```

`check --connect` reads the account's **real grants** (`SHOW GRANTS`) and reports anything beyond
SELECT on the allow-listed databases. Setting `MYSQL_RO_USER` alone proves nothing about what
that account may actually do, so the server runs the same audit at start-up.

Which MySQL account is used:

| Configuration | Account used | Start-up behaviour |
|---|---|---|
| `MYSQL_RO_USER` + `MYSQL_RO_PASSWORD` | that account (intended SELECT-only) | silent if it is not an admin name; grants audited |
| only `MYSQL_PASSWORD` (`MYSQL_USER` unset) | **`root`** (legacy fallback) | `SECURITY` warning every start |
| `MYSQL_USER` + `MYSQL_PASSWORD` | that account (legacy fallback) | warning (louder for `root`/`admin`) |
| any of the above + `QC_REQUIRE_READONLY_MYSQL=true` | only a dedicated `MYSQL_RO_USER` | the legacy fallback is **refused** |

Limits of this layer (details in [docs/SECURITY.md](docs/SECURITY.md)):

* A cartesian join is a *semantic* problem a regex cannot decide, so it is bounded by resource
  controls instead: a 10 s server-side statement timeout, a 30 s socket timeout, and a
  server-side row cap (`sql_select_limit`) plus the application's 1000-row cap.
* The MySQL driver still buffers a result before the 1000-row cap is applied. The server-side
  row cap makes this hard to trigger by accident, but an explicit large `LIMIT` overrides it.
  Streaming results is tracked as a P1 follow-up.

## Credentials and secrets

Nothing in the repository contains a credential. Every secret comes from the environment
(`app/.env`, which is git-ignored, or your platform's secret store).

| Variable | Used by | Notes |
|---|---|---|
| `GOOGLE_API_KEY` | web app | LLM access |
| `MYSQL_RO_USER`, `MYSQL_RO_PASSWORD` | web app, read-only tools | **preferred**: SELECT-only account |
| `MYSQL_USER`, `MYSQL_PASSWORD` | web app (legacy fallback), admin tools (fallback) | administrative; avoid in production |
| `MYSQL_ADMIN_USER`, `MYSQL_ADMIN_PASSWORD` | offline `load_*` / `fix_*` / `repair_*` scripts only | never read by the web app |
| `QC_OWNER_SALT` | web app | long random value; set before the first upload |

* Offline scripts need a privileged account by design, so they take it **explicitly** from
  `MYSQL_ADMIN_*` (falling back to the legacy pair with a printed notice), and an empty password
  is an error, never assumed.
* Error messages, log records (including tracebacks) and LLM prompts are passed through a
  redaction layer that masks configured secret values, `user:password@host` URLs and
  `password=...` pairs. API responses never include credentials, connection strings, file paths
  or owner hashes.
* `python -m app.hardening_tools scan-secrets` scans the working tree for hard-coded credentials
  (values are never printed). The test suite runs the same scan, so a committed secret fails the tests.
  The working-tree scan **cannot see git history**; see [docs/SECURITY.md](docs/SECURITY.md) for
  how to check and clean history, and why rotation is the only real fix.



## Setup

```bash
# backend
pip install -r backend/requirements.txt
cp .env.example app/.env        # then fill in the values
uvicorn backend.main:app --reload

# frontend
cd frontend
npm install
npm run dev                     # set VITE_API_URL if the API is not on 127.0.0.1:8000
```

SQLite-only deployment (no MySQL required): set `QC_ENABLE_BUILTIN_DBS=false`.

**Production checklist**

* Set `QC_OWNER_SALT` to a long random string *before* the first upload. Changing it
  later orphans existing uploads.
* Set `QC_CORS_ORIGINS` to your real frontend origin(s).
* Put the API behind HTTPS and a reverse proxy that enforces request-size limits and
  rate limiting.
* Back up / persist `QC_DATA_DIR` (it holds the registry, uploaded databases and Chroma).

All settings are documented in `.env.example`.

## API additions

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/databases/upload` | multipart `files` (+ optional `name`); returns the database and a table summary |
| `GET` | `/api/databases` | built-in names plus the caller's `user_databases` |
| `GET` | `/api/databases/limits` | limits and accepted extensions (drives the UI) |
| `DELETE` | `/api/databases/{id}` | delete one of the caller's databases |

All requests carry an `X-Client-Id` header (generated once per browser by
`frontend/src/api.ts`). `POST /api/query` accepts a user database id in `database`.

## Tests

```bash
python -m unittest discover -s tests -v     # standard library + pandas/openpyxl
```

Covers the read-only engine and each of its defence layers, registry and access control,
ingestion (hostile SQLite files, CSV/Excel, FK inference), schema documents, indexing
isolation, service lifecycle and rollback, the MySQL allow-list / guardrails / session limits,
the privilege audit, and the credential hygiene checks (hard-coded secrets, log and API
redaction, `.gitignore` behaviour against real `git`, frontend). One test is skipped unless
`frontend/dist` has been built.

## Known limitations and next steps

* **Identity is anonymous.** The client id is a random value kept in `localStorage`.
  Clearing site data loses access to uploads until they expire. For a real product, add
  accounts and replace `hash_owner()` / `getClientId()` with the authenticated user.
* **History is in memory** (unchanged from before), so it resets on restart and does not
  scale past one process. Move it to a database.
* **No rate limiting or job queue.** Indexing runs inside the upload request. Large
  uploads should move to a background worker with progress reporting.
* **SQLite/CSV/Excel only.** Live connections to customer Postgres/MySQL servers are a
  separate, bigger feature (credential storage, network egress rules, per-dialect
  guardrails).
* **Accuracy on messy real-world schemas will be lower** than on the clean Spider
  databases. Column descriptions, a business glossary and per-database evaluation are the
  highest-value improvements.
