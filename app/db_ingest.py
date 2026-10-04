"""
Turn an untrusted upload into a sanitized, read-only-friendly SQLite database
and describe it to the RAG layer.

Supported inputs
    * one SQLite file        (.sqlite, .sqlite3, .db)
    * one or more tables     (.csv, .tsv, .xlsx  -> one table per file/sheet)

Nothing from an uploaded SQLite file is trusted. Instead of using the file
directly we *rebuild* a fresh database: only plain tables are copied, from
column metadata (PRAGMA) rather than from the original ``CREATE`` text, so
triggers, views, virtual tables, generated columns, custom collations,
defaults and any other executable schema content are dropped. Identifiers are
normalised to ``[A-Za-z0-9_]`` which is what the rest of the pipeline
(validator, guardrails, prompts) expects.

Standard library at import time; pandas/openpyxl are imported lazily and only
for CSV/Excel uploads. The vector store is injected, so this module does not
import Chroma or LangChain.
"""

from __future__ import annotations

import csv
import datetime as _dt
import decimal
import math
import os
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from app.db_engines import SQLITE_MAGIC, open_readonly_connection

# ---------------------------------------------------------------------------
# limits (override with environment variables)
# ---------------------------------------------------------------------------

MAX_UPLOAD_BYTES = int(
    os.getenv("QC_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))
)
MAX_FILES = int(os.getenv("QC_MAX_UPLOAD_FILES", "20"))
MAX_TABLES = int(os.getenv("QC_MAX_TABLES", "100"))
MAX_COLUMNS = int(os.getenv("QC_MAX_COLUMNS", "200"))
MAX_ROWS_PER_TABLE = int(os.getenv("QC_MAX_ROWS_PER_TABLE", "500000"))
MAX_TOTAL_ROWS = int(os.getenv("QC_MAX_TOTAL_ROWS", "2000000"))
INGEST_TIMEOUT_SECONDS = float(os.getenv("QC_INGEST_TIMEOUT_SECONDS", "120"))

SAMPLE_VALUES_PER_COLUMN = 5
SAMPLE_VALUE_MAX_CHARS = 40
MAX_BLOB_BYTES = 64 * 1024

SQLITE_EXTENSIONS = {".sqlite", ".sqlite3", ".db"}
TABULAR_EXTENSIONS = {".csv", ".tsv", ".xlsx"}
UNSUPPORTED_HINTS = {
    ".sql": "SQL dumps are not supported yet. Upload a .sqlite/.db file "
            "or CSV/Excel files instead.",
    ".xls": "Legacy .xls files are not supported. Save as .xlsx or .csv.",
    ".zip": "Archives are not supported. Upload the files directly.",
}


class IngestError(ValueError):
    """The upload is invalid, unsupported or exceeds a limit."""


# ---------------------------------------------------------------------------
# data model
# ---------------------------------------------------------------------------

@dataclass
class ColumnInfo:
    name: str
    original: str
    type: str          # display type, e.g. INTEGER, VARCHAR(20), ANY
    ddl_type: str      # what goes into CREATE TABLE ('' for untyped)
    pk_order: int = 0  # 1-based position in the primary key, 0 = not PK


@dataclass
class ForeignKey:
    column: str
    ref_table: str
    ref_column: str
    inferred: bool = False


@dataclass
class TableInfo:
    name: str
    original: str
    columns: list[ColumnInfo] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    row_count: int = 0

    @property
    def primary_key(self) -> list[str]:
        pk = [c for c in self.columns if c.pk_order]
        return [c.name for c in sorted(pk, key=lambda c: c.pk_order)]


@dataclass
class BuildReport:
    source_type: str
    tables: list[TableInfo]
    total_rows: int
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# identifier / type helpers
# ---------------------------------------------------------------------------

def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sanitize_identifier(raw: Any, fallback: str, used: set[str]) -> str:
    """Return a unique ``[A-Za-z0-9_]`` identifier derived from ``raw``."""

    text = unicodedata.normalize("NFKD", str(raw))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")

    if not text:
        text = fallback

    if text[0].isdigit():
        text = f"{fallback}_{text}"

    text = text[:64].rstrip("_") or fallback

    candidate = text
    counter = 2

    while candidate.lower() in used:
        candidate = f"{text}_{counter}"
        counter += 1

    used.add(candidate.lower())

    return candidate


_SAFE_TYPE_RE = re.compile(
    r"^[A-Za-z][A-Za-z ]{0,30}(\(\s*\d{1,4}(\s*,\s*\d{1,4})?\s*\))?$"
)


def _affinity(declared: str) -> str:
    """SQLite's documented type-affinity rules."""

    d = declared.upper()

    if "INT" in d:
        return "INTEGER"
    if "CHAR" in d or "CLOB" in d or "TEXT" in d:
        return "TEXT"
    if "BLOB" in d or not d:
        return "BLOB"
    if "REAL" in d or "FLOA" in d or "DOUB" in d:
        return "REAL"

    return "NUMERIC"


def normalize_declared_type(declared: str | None) -> tuple[str, str]:
    """Return ``(display_type, ddl_type)`` for a declared column type."""

    text = re.sub(r"\s+", " ", (declared or "").strip())

    if not text:
        return "ANY", ""

    if _SAFE_TYPE_RE.match(text):
        return text.upper(), text.upper()

    aff = _affinity(text)

    return aff, aff


# ---------------------------------------------------------------------------
# value conversion
# ---------------------------------------------------------------------------

def _py_value(value: Any) -> Any:
    """Convert pandas/numpy/other values into something sqlite3 can bind."""

    if value is None:
        return None

    # pandas NaT/NA subclass datetime / are truthy oddities: check by name.
    if type(value).__name__ in {"NaTType", "NAType"}:
        return None

    if isinstance(value, bool):
        return int(value)

    if isinstance(value, (int, str)):
        return value

    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value

    if isinstance(value, bytes):
        return value if len(value) <= MAX_BLOB_BYTES else None

    if isinstance(value, decimal.Decimal):
        return float(value)

    if isinstance(value, _dt.datetime):
        return value.isoformat(sep=" ", timespec="seconds")

    if isinstance(value, _dt.date):
        return value.isoformat()

    if isinstance(value, _dt.time):
        return value.isoformat(timespec="seconds")

    # numpy scalars
    item = getattr(value, "item", None)

    if callable(item):
        try:
            return _py_value(item())
        except Exception:
            pass

    return str(value)


# ---------------------------------------------------------------------------
# upload classification
# ---------------------------------------------------------------------------

def classify_upload(paths: list[Path]) -> str:
    """Validate the set of uploaded files and return 'sqlite' or 'tabular'."""

    if not paths:
        raise IngestError("No file was uploaded.")

    if len(paths) > MAX_FILES:
        raise IngestError(f"Too many files (maximum {MAX_FILES}).")

    kinds = set()
    total = 0

    for path in paths:
        ext = path.suffix.lower()
        size = path.stat().st_size
        total += size

        if size == 0:
            raise IngestError(f"'{path.name}' is empty.")

        if ext in UNSUPPORTED_HINTS:
            raise IngestError(UNSUPPORTED_HINTS[ext])

        if ext in SQLITE_EXTENSIONS:
            kinds.add("sqlite")
        elif ext in TABULAR_EXTENSIONS:
            kinds.add("tabular")
        else:
            raise IngestError(
                f"Unsupported file type '{ext or path.name}'. Upload a "
                ".sqlite/.db file, or .csv/.tsv/.xlsx files."
            )

    if total > MAX_UPLOAD_BYTES:
        raise IngestError(
            f"Upload is too large (maximum "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB)."
        )

    if len(kinds) > 1:
        raise IngestError(
            "Upload either one SQLite file or CSV/Excel files, not both."
        )

    kind = kinds.pop()

    if kind == "sqlite" and len(paths) != 1:
        raise IngestError("Upload exactly one SQLite file.")

    # Verify content, never trust the extension alone.
    for path in paths:
        with open(path, "rb") as handle:
            head = handle.read(16)

        ext = path.suffix.lower()

        if ext in SQLITE_EXTENSIONS and head != SQLITE_MAGIC:
            raise IngestError(
                f"'{path.name}' is not a valid SQLite database file."
            )

        if ext == ".xlsx" and not head.startswith(b"PK\x03\x04"):
            raise IngestError(f"'{path.name}' is not a valid .xlsx file.")

        if ext in {".csv", ".tsv"} and (
            head.startswith(SQLITE_MAGIC[:6]) or head.startswith(b"PK")
        ):
            raise IngestError(f"'{path.name}' is not a text CSV file.")

    return kind


# ---------------------------------------------------------------------------
# building the sanitized database
# ---------------------------------------------------------------------------

def build_database(paths: list[Path], dest: Path) -> BuildReport:
    """
    Convert an upload into a sanitized SQLite database at ``dest``.

    On any failure ``dest`` (and scratch files) are removed and
    ``IngestError`` is raised.
    """

    kind = classify_upload(paths)

    dest.parent.mkdir(parents=True, exist_ok=True)

    scratch = dest.with_suffix(".scratch")
    final_tmp = dest.with_suffix(".building")

    for leftover in (dest, scratch, final_tmp):
        leftover.unlink(missing_ok=True)

    succeeded = False

    try:
        if kind == "sqlite":
            report = _build_from_sqlite(paths[0], scratch)
        else:
            report = _build_from_tabular(paths, scratch)

        _infer_foreign_keys(scratch, report)

        if any(
            fk.inferred for t in report.tables for fk in t.foreign_keys
        ):
            _rebuild_with_foreign_keys(scratch, final_tmp, report)
            os.replace(final_tmp, dest)
        else:
            os.replace(scratch, dest)

        succeeded = True
        return report

    except IngestError:
        raise

    except sqlite3.DatabaseError as exc:
        raise IngestError(
            "The database could not be read. It may be corrupt, "
            f"encrypted or unsupported ({exc.__class__.__name__})."
        ) from exc

    except MemoryError as exc:
        raise IngestError("The upload is too large to process.") from exc

    finally:
        for leftover in (scratch, final_tmp):
            leftover.unlink(missing_ok=True)

        if not succeeded:
            dest.unlink(missing_ok=True)


def _create_table_sql(table: TableInfo) -> str:
    parts = []

    for col in table.columns:
        piece = quote_ident(col.name)

        if col.ddl_type:
            piece += " " + col.ddl_type

        parts.append(piece)

    if table.primary_key:
        parts.append(
            "PRIMARY KEY ("
            + ", ".join(quote_ident(c) for c in table.primary_key)
            + ")"
        )

    for fk in table.foreign_keys:
        parts.append(
            f"FOREIGN KEY ({quote_ident(fk.column)}) "
            f"REFERENCES {quote_ident(fk.ref_table)} "
            f"({quote_ident(fk.ref_column)})"
        )

    return (
        f"CREATE TABLE {quote_ident(table.name)} (\n  "
        + ",\n  ".join(parts)
        + "\n)"
    )


def _insert_batches(
    conn: sqlite3.Connection,
    table: TableInfo,
    rows: Iterable[tuple],
    row_cap: int,
    warnings: list[str],
) -> int:
    placeholders = ", ".join("?" for _ in table.columns)
    sql = f"INSERT INTO {quote_ident(table.name)} VALUES ({placeholders})"

    count = 0
    batch: list[tuple] = []
    truncated = False

    for row in rows:
        if count >= row_cap:
            truncated = True
            break

        batch.append(tuple(_py_value(v) for v in row))
        count += 1

        if len(batch) >= 5000:
            conn.executemany(sql, batch)
            batch.clear()

    if batch:
        conn.executemany(sql, batch)

    if truncated:
        warnings.append(
            f"Table '{table.original}' was truncated to the first "
            f"{row_cap:,} rows."
        )

    return count


def _new_dest(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA foreign_keys = OFF")
    return conn


# ----- SQLite source -------------------------------------------------------

def _build_from_sqlite(src: Path, dest: Path) -> BuildReport:
    warnings: list[str] = []

    source = open_readonly_connection(src, timeout=INGEST_TIMEOUT_SECONDS)
    out = _new_dest(dest)

    try:
        listing = source.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' "
            "ORDER BY name"
        ).fetchall()

        skipped_virtual = [
            n for n, sql in listing
            if (sql or "").lstrip().upper().startswith("CREATE VIRTUAL")
        ]
        names = [n for n, _ in listing if n not in skipped_virtual]

        if skipped_virtual:
            warnings.append(
                "Skipped virtual tables: " + ", ".join(skipped_virtual)
            )

        if not names:
            raise IngestError("The database contains no tables.")

        if len(names) > MAX_TABLES:
            raise IngestError(
                f"Too many tables ({len(names)}); the maximum is "
                f"{MAX_TABLES}."
            )

        # ---- names + columns
        used_tables: set[str] = set()
        infos: dict[str, TableInfo] = {}
        column_maps: dict[str, dict[str, str]] = {}

        for original in names:
            safe_table = sanitize_identifier(original, "table", used_tables)

            cols = source.execute(
                f"PRAGMA table_info({quote_ident(original)})"
            ).fetchall()

            if not cols:
                warnings.append(f"Skipped table '{original}' (no columns).")
                used_tables.discard(safe_table.lower())
                continue

            if len(cols) > MAX_COLUMNS:
                raise IngestError(
                    f"Table '{original}' has {len(cols)} columns; the "
                    f"maximum is {MAX_COLUMNS}."
                )

            used_cols: set[str] = set()
            columns: list[ColumnInfo] = []
            mapping: dict[str, str] = {}

            for _cid, cname, ctype, _nn, _dflt, pk in cols:
                safe_col = sanitize_identifier(cname, "col", used_cols)
                display, ddl = normalize_declared_type(ctype)
                columns.append(
                    ColumnInfo(
                        name=safe_col,
                        original=str(cname),
                        type=display,
                        ddl_type=ddl,
                        pk_order=int(pk or 0),
                    )
                )
                mapping[str(cname)] = safe_col

            infos[original] = TableInfo(
                name=safe_table, original=original, columns=columns
            )
            column_maps[original] = mapping

        # ---- declared foreign keys (only between copied tables)
        for original, table in infos.items():
            fks = source.execute(
                f"PRAGMA foreign_key_list({quote_ident(original)})"
            ).fetchall()

            per_id: dict[int, list[tuple]] = {}

            for fk in fks:
                per_id.setdefault(fk[0], []).append(fk)

            for group in per_id.values():
                if len(group) != 1:
                    warnings.append(
                        f"Ignored a composite foreign key on '{original}'."
                    )
                    continue

                _id, _seq, ref_table, from_col, to_col = group[0][:5]

                if ref_table not in infos or from_col not in column_maps[original]:
                    continue

                ref_info = infos[ref_table]

                if to_col is None:
                    pk = ref_info.primary_key

                    if len(pk) != 1:
                        continue

                    ref_col = pk[0]
                else:
                    ref_col = column_maps[ref_table].get(str(to_col))

                    if ref_col is None:
                        continue

                table.foreign_keys.append(
                    ForeignKey(
                        column=column_maps[original][from_col],
                        ref_table=ref_info.name,
                        ref_column=ref_col,
                    )
                )

        # ---- create + copy
        for table in infos.values():
            out.execute(_create_table_sql(table))

        total = 0

        for original, table in infos.items():
            select_cols = ", ".join(
                quote_ident(c.original) for c in table.columns
            )
            cursor = source.execute(
                f"SELECT {select_cols} FROM {quote_ident(original)}"
            )

            remaining = MAX_TOTAL_ROWS - total

            if remaining <= 0:
                raise IngestError(
                    f"The database has more than {MAX_TOTAL_ROWS:,} rows "
                    "in total, which is over the limit."
                )

            cap = min(MAX_ROWS_PER_TABLE, remaining)
            table.row_count = _insert_batches(
                out, table, _iter_cursor(cursor), cap, warnings
            )
            total += table.row_count

        out.commit()

        return BuildReport(
            source_type="sqlite",
            tables=list(infos.values()),
            total_rows=total,
            warnings=warnings,
        )

    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower():
            raise IngestError(
                "Processing the database took too long and was cancelled."
            ) from exc
        raise

    finally:
        source.close()
        out.close()


def _iter_cursor(cursor: sqlite3.Cursor) -> Iterator[tuple]:
    while True:
        chunk = cursor.fetchmany(5000)

        if not chunk:
            return

        yield from chunk


# ----- CSV / Excel source --------------------------------------------------

def _sniff_delimiter(path: Path) -> str:
    if path.suffix.lower() == ".tsv":
        return "\t"

    with open(path, "rb") as handle:
        sample = handle.read(20000).decode("utf-8", errors="ignore")

    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _read_csv(path: Path):
    import pandas as pd

    sep = _sniff_delimiter(path)
    last_error: Exception | None = None

    for encoding in ("utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(
                path,
                sep=sep,
                encoding=encoding,
                nrows=MAX_ROWS_PER_TABLE + 1,
                low_memory=False,
                on_bad_lines="skip",
            )
        except UnicodeDecodeError as exc:
            last_error = exc
        except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
            raise IngestError(
                f"Could not parse '{path.name}' as CSV: {exc}"
            ) from exc

    raise IngestError(
        f"Could not decode '{path.name}' ({last_error.__class__.__name__})."
    )


def _read_excel(path: Path) -> dict[str, Any]:
    import pandas as pd

    try:
        sheets = pd.read_excel(
            path,
            sheet_name=None,
            engine="openpyxl",
            nrows=MAX_ROWS_PER_TABLE + 1,
        )
    except Exception as exc:  # openpyxl raises many types
        raise IngestError(
            f"Could not read '{path.name}' as an Excel workbook."
        ) from exc

    return sheets


def _frame_column_type(series) -> tuple[str, str]:
    import pandas as pd

    dtype = series.dtype

    if pd.api.types.is_bool_dtype(dtype):
        return "INTEGER", "INTEGER"

    if pd.api.types.is_integer_dtype(dtype):
        return "INTEGER", "INTEGER"

    if pd.api.types.is_float_dtype(dtype):
        non_null = series.dropna()

        if len(non_null) and (non_null == non_null.round()).all() and (
            non_null.abs() < 2**53
        ).all():
            # Integers stored as floats because of blanks.
            return "INTEGER", "INTEGER"

        return "REAL", "REAL"

    if pd.api.types.is_datetime64_any_dtype(dtype):
        non_null = series.dropna()

        if len(non_null) and (non_null == non_null.dt.normalize()).all():
            return "DATE", "DATE"

        return "DATETIME", "DATETIME"

    return "TEXT", "TEXT"


def _frame_to_table(
    df,
    table_name: str,
    original: str,
    warnings: list[str],
) -> tuple[TableInfo, list[tuple]]:
    import pandas as pd

    if df.shape[1] == 0:
        raise IngestError(f"'{original}' has no columns.")

    if df.shape[1] > MAX_COLUMNS:
        raise IngestError(
            f"'{original}' has {df.shape[1]} columns; the maximum is "
            f"{MAX_COLUMNS}."
        )

    if len(df) > MAX_ROWS_PER_TABLE:
        warnings.append(
            f"Table '{original}' was truncated to the first "
            f"{MAX_ROWS_PER_TABLE:,} rows."
        )
        df = df.iloc[:MAX_ROWS_PER_TABLE]

    used: set[str] = set()
    columns: list[ColumnInfo] = []

    for label in df.columns:
        display, ddl = _frame_column_type(df[label])
        columns.append(
            ColumnInfo(
                name=sanitize_identifier(label, "col", used),
                original=str(label),
                type=display,
                ddl_type=ddl,
            )
        )

    converted = df.copy()

    for label in converted.columns:
        if pd.api.types.is_datetime64_any_dtype(converted[label].dtype):
            fmt = (
                "%Y-%m-%d"
                if columns[list(df.columns).index(label)].type == "DATE"
                else "%Y-%m-%d %H:%M:%S"
            )
            converted[label] = converted[label].dt.strftime(fmt)

    as_object = converted.astype(object).where(converted.notna(), None)
    rows = [tuple(rec) for rec in as_object.itertuples(index=False, name=None)]

    table = TableInfo(
        name=table_name,
        original=original,
        columns=columns,
        row_count=len(rows),
    )

    return table, rows


def _build_from_tabular(paths: list[Path], dest: Path) -> BuildReport:
    warnings: list[str] = []

    frames: list[tuple[str, Any]] = []   # (original name, DataFrame)

    for path in paths:
        ext = path.suffix.lower()

        if ext == ".xlsx":
            for sheet, df in _read_excel(path).items():
                if df.shape[1] == 0:
                    warnings.append(f"Skipped empty sheet '{sheet}'.")
                    continue

                label = (
                    sheet if len(paths) == 1 else f"{path.stem}_{sheet}"
                )
                frames.append((label, df))
        else:
            frames.append((path.stem, _read_csv(path)))

    if not frames:
        raise IngestError("No usable tables were found in the upload.")

    if len(frames) > MAX_TABLES:
        raise IngestError(
            f"Too many tables ({len(frames)}); the maximum is {MAX_TABLES}."
        )

    used_tables: set[str] = set()
    tables: list[TableInfo] = []
    data: list[list[tuple]] = []

    for original, df in frames:
        name = sanitize_identifier(original, "table", used_tables)
        table, rows = _frame_to_table(df, name, original, warnings)
        tables.append(table)
        data.append(rows)

    total = sum(len(r) for r in data)

    if total > MAX_TOTAL_ROWS:
        raise IngestError(
            f"The upload has {total:,} rows in total; the limit is "
            f"{MAX_TOTAL_ROWS:,}."
        )

    _detect_primary_keys(tables, data)

    out = _new_dest(dest)

    try:
        for table in tables:
            out.execute(_create_table_sql(table))

        for table, rows in zip(tables, data):
            _insert_batches(out, table, iter(rows), len(rows), warnings)

        out.commit()

    finally:
        out.close()

    return BuildReport(
        source_type="tabular",
        tables=tables,
        total_rows=total,
        warnings=warnings,
    )


def _detect_primary_keys(
    tables: list[TableInfo], data: list[list[tuple]]
) -> None:
    """
    Flat files carry no keys. Mark a column as PRIMARY KEY when it looks like
    an identifier (named ``id`` / ``<table>_id`` / ``<something>_id`` first
    column) and is unique + non-null across all rows.
    """

    for table, rows in zip(tables, data):
        if not rows:
            continue

        for index, col in enumerate(table.columns[:3]):
            lname = col.name.lower()
            stem = table.name.lower().rstrip("s")

            looks_like_id = (
                lname == "id"
                or lname.endswith("_id")
                or lname == f"{stem}id"
                or (lname.startswith(stem) and lname.endswith("id"))
            )

            if not looks_like_id:
                continue

            values = [row[index] for row in rows]

            if None in values or len(set(values)) != len(values):
                continue

            col.pk_order = 1
            break


# ---------------------------------------------------------------------------
# foreign-key inference (for sources that declare none)
# ---------------------------------------------------------------------------

def _plural_candidates(stem: str) -> set[str]:
    stem = stem.lower()
    candidates = {stem, stem + "s", stem + "es"}

    if stem.endswith("y"):
        candidates.add(stem[:-1] + "ies")

    return candidates


def _infer_foreign_keys(db_path: Path, report: BuildReport) -> None:
    """
    Add *inferred* foreign keys, verified against the data, for columns that
    have no declared key. Conservative on purpose: a wrong relationship is
    worse than a missing one.
    """

    tables = {t.name.lower(): t for t in report.tables}

    if len(tables) < 2:
        return

    conn = sqlite3.connect(db_path)

    try:
        unique_cache: dict[tuple[str, str], bool] = {}

        def is_unique(table: str, col: str) -> bool:
            key = (table, col)

            if key not in unique_cache:
                total, distinct, non_null = conn.execute(
                    f"SELECT COUNT(*), COUNT(DISTINCT {quote_ident(col)}), "
                    f"COUNT({quote_ident(col)}) FROM {quote_ident(table)}"
                ).fetchone()
                unique_cache[key] = (
                    total > 0 and total == distinct == non_null
                )

            return unique_cache[key]

        def inclusion(child: str, ccol: str, parent: str, pcol: str) -> float:
            non_null = conn.execute(
                f"SELECT COUNT({quote_ident(ccol)}) "
                f"FROM {quote_ident(child)}"
            ).fetchone()[0]

            if not non_null:
                return 0.0

            matched = conn.execute(
                f"SELECT COUNT(*) FROM {quote_ident(child)} "
                f"WHERE {quote_ident(ccol)} IS NOT NULL AND "
                f"{quote_ident(ccol)} IN "
                f"(SELECT {quote_ident(pcol)} FROM {quote_ident(parent)})"
            ).fetchone()[0]

            return matched / non_null

        def compatible(a: ColumnInfo, b: ColumnInfo) -> bool:
            numeric = {"INTEGER", "REAL", "NUMERIC"}
            aa, bb = _affinity(a.type), _affinity(b.type)
            return aa == bb or (aa in numeric and bb in numeric)

        for child in report.tables:
            if child.foreign_keys:
                continue    # source declared keys; trust them

            fk_columns: set[str] = set()

            for ccol in child.columns:
                lname = ccol.name.lower()
                best: tuple[float, ForeignKey] | None = None

                # Rule A: same column name in another table (unique there)
                # Rule B: <stem>_id -> table <stem>/<stem>s with column id
                candidates: list[tuple[TableInfo, ColumnInfo]] = []

                for parent in report.tables:
                    if parent is child:
                        continue

                    for pcol in parent.columns:
                        pname = pcol.name.lower()

                        same_name = pname == lname and (
                            lname.endswith("id")
                            or lname.endswith(("_code", "_key", "_no"))
                            or (
                                len(parent.primary_key) == 1
                                and parent.primary_key[0].lower() == lname
                            )
                        )

                        stem_rule = (
                            lname.endswith("_id")
                            and pname == "id"
                            and parent.name.lower()
                            in _plural_candidates(lname[:-3])
                        )

                        if same_name or stem_rule:
                            candidates.append((parent, pcol))

                for parent, pcol in candidates:
                    if not compatible(ccol, pcol):
                        continue

                    if not is_unique(parent.name, pcol.name):
                        continue

                    ratio = inclusion(
                        child.name, ccol.name, parent.name, pcol.name
                    )

                    if ratio >= 0.9 and (best is None or ratio > best[0]):
                        best = (
                            ratio,
                            ForeignKey(
                                column=ccol.name,
                                ref_table=parent.name,
                                ref_column=pcol.name,
                                inferred=True,
                            ),
                        )

                if best and ccol.name not in fk_columns:
                    fk_columns.add(ccol.name)
                    child.foreign_keys.append(best[1])

    finally:
        conn.close()


def _rebuild_with_foreign_keys(
    scratch: Path, final: Path, report: BuildReport
) -> None:
    """Recreate every table with FK clauses and copy the data across."""

    out = _new_dest(final)

    try:
        out.execute("ATTACH DATABASE ? AS scratch", (str(scratch),))

        for table in report.tables:
            out.execute(_create_table_sql(table))

        for table in report.tables:
            out.execute(
                f"INSERT INTO main.{quote_ident(table.name)} "
                f"SELECT * FROM scratch.{quote_ident(table.name)}"
            )

        out.commit()
        out.execute("DETACH DATABASE scratch")

    finally:
        out.close()


# ---------------------------------------------------------------------------
# schema documents for the RAG layer
# ---------------------------------------------------------------------------

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")


def clean_text(value: Any, limit: int = SAMPLE_VALUE_MAX_CHARS) -> str:
    """Flatten a value into a short single line (prompt-injection hygiene)."""

    text = _CONTROL_RE.sub(" ", str(value)).strip()

    if len(text) > limit:
        text = text[: limit - 1] + "…"

    return text


def _sample_values(
    conn: sqlite3.Connection, table: str, column: str
) -> list[str]:
    try:
        rows = conn.execute(
            f"SELECT DISTINCT {quote_ident(column)} FROM {quote_ident(table)} "
            f"WHERE {quote_ident(column)} IS NOT NULL "
            f"LIMIT {SAMPLE_VALUES_PER_COLUMN}"
        ).fetchall()
    except sqlite3.Error:
        return []

    values = []

    for (value,) in rows:
        if isinstance(value, bytes):
            continue

        cleaned = clean_text(value)

        if cleaned:
            values.append(cleaned.replace(",", ";"))

    return values


def build_schema_documents(
    db_id: str, db_path: Path, tables: list[TableInfo]
) -> list[dict[str, Any]]:
    """
    Produce one document per table in the exact layout the existing
    ``rag_node`` / ``score_table`` code already understands::

        DATABASE: ...
        TABLE: ...
        COLUMNS:
        - name (TYPE)
        PRIMARY KEYS: ...
        FOREIGN KEYS:
        - col -> table.col
        SAMPLE VALUES:
        - col: a, b, c
    """

    conn = open_readonly_connection(db_path, timeout=30)
    documents: list[dict[str, Any]] = []

    try:
        for table in tables:
            lines = [f"DATABASE: {db_id}", f"TABLE: {table.name}"]

            if table.original != table.name:
                lines.append(f"ORIGINAL NAME: {clean_text(table.original, 80)}")

            lines.append("COLUMNS:")

            for col in table.columns:
                lines.append(f"- {col.name} ({col.type})")

            renamed = [
                f"{c.name} = {clean_text(c.original, 80)}"
                for c in table.columns
                if c.original != c.name
            ]

            if renamed:
                lines.append("ORIGINAL COLUMN NAMES: " + "; ".join(renamed))

            if table.primary_key:
                lines.append("PRIMARY KEYS: " + ", ".join(table.primary_key))

            if table.foreign_keys:
                lines.append("FOREIGN KEYS:")

                for fk in table.foreign_keys:
                    suffix = " (inferred)" if fk.inferred else ""
                    lines.append(
                        f"- {fk.column} -> {fk.ref_table}.{fk.ref_column}"
                        f"{suffix}"
                    )

            samples = []

            for col in table.columns:
                values = _sample_values(conn, table.name, col.name)

                if values:
                    samples.append(f"- {col.name}: " + ", ".join(values))

            if samples:
                lines.append("SAMPLE VALUES:")
                lines.extend(samples)

            documents.append(
                {
                    "id": f"{db_id}::{table.name}",
                    "text": "\n".join(lines),
                    "metadata": {
                        "database": db_id,
                        "table": table.name,
                        "kind": "user",
                    },
                }
            )

    finally:
        conn.close()

    return documents


# ---------------------------------------------------------------------------
# vector-store helpers (store is injected: a langchain Chroma or a fake)
# ---------------------------------------------------------------------------

def remove_from_index(store: Any, db_id: str) -> None:
    existing = store.get(where={"database": db_id})
    ids = (existing or {}).get("ids") or []

    if ids:
        store.delete(ids=ids)


def index_documents(
    store: Any, db_id: str, documents: list[dict[str, Any]]
) -> None:
    remove_from_index(store, db_id)

    if not documents:
        return

    store.add_texts(
        texts=[d["text"] for d in documents],
        metadatas=[d["metadata"] for d in documents],
        ids=[d["id"] for d in documents],
    )
