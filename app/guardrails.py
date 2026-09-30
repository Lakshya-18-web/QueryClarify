"""QueryClarify input, SQL, and output guardrails."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class GuardrailResult:
    allowed: bool
    reason: str
    category: str


MAX_QUESTION_LENGTH = 2000
MAX_RESULT_ROWS = 1000
MAX_RESULT_CELL_LENGTH = 5000


# ============================================================
# INPUT GUARDRAILS
# ============================================================

BLOCKED_INPUT_PATTERNS = [
    (
        r"ignore\s+(all\s+)?previous\s+instructions",
        "prompt_injection",
    ),
    (
        r"ignore\s+(all\s+)?prior\s+instructions",
        "prompt_injection",
    ),
    (
        r"forget\s+(all\s+)?previous\s+instructions",
        "prompt_injection",
    ),
    (
        r"system\s+prompt",
        "prompt_injection",
    ),
    (
        r"developer\s+message",
        "prompt_injection",
    ),
    (
        r"reveal\s+(the\s+)?(system|developer)\s+prompt",
        "prompt_injection",
    ),
    (
        r"show\s+me\s+(your|the)\s+prompt",
        "prompt_injection",
    ),
    (
        r"drop\s+(the\s+)?database",
        "destructive_request",
    ),
    (
        r"drop\s+table",
        "destructive_request",
    ),
    (
        r"delete\s+(all|the)\s+rows",
        "destructive_request",
    ),
    (
        r"truncate\s+(the\s+)?table",
        "destructive_request",
    ),
    (
        r"alter\s+(the\s+)?table",
        "destructive_request",
    ),
    (
        r"grant\s+.*\s+privileges",
        "privilege_request",
    ),
    (
        r"revoke\s+.*\s+privileges",
        "privilege_request",
    ),
]


FORBIDDEN_SQL = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "REPLACE",
    "GRANT",
    "REVOKE",
    "CALL",
    "LOAD",
    "SET",
    "USE",
    "HANDLER",
    "LOCK",
    "UNLOCK",
}


SYSTEM_DATABASES = {
    "MYSQL",
    "INFORMATION_SCHEMA",
    "PERFORMANCE_SCHEMA",
    "SYS",
}


SQL_INJECTION_PATTERNS = [
    r"\bOR\s+1\s*=\s*1\b",
    r"\bAND\s+1\s*=\s*1\b",
    r"['\"]\s*OR\s*['\"]?[^ \n]{0,40}['\"]?\s*=\s*['\"]?[^ \n]{0,40}['\"]?",
]


# ============================================================
# GENERAL HELPERS
# ============================================================

def _strip_sql_strings(sql: str) -> str:
    return re.sub(
        r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"",
        "'VALUE'",
        sql,
    )


def _contains_forbidden_keyword(sql: str) -> str | None:
    normalized = _strip_sql_strings(sql.upper())

    for keyword in FORBIDDEN_SQL:
        if re.search(
            rf"\b{re.escape(keyword)}\b",
            normalized,
        ):
            return keyword

    return None


# ============================================================
# IDENTIFIER HELPERS
# ============================================================

def _identifier_pattern() -> str:
    return r"(?:`([^`]+)`|([A-Za-z0-9_]+))"


def _clean_identifier(value: str | None) -> str:
    if not value:
        return ""

    return value.strip().strip("`").lower()


def _extract_table_aliases(sql: str) -> set[str]:
    """
    Extract aliases defined in FROM/JOIN clauses.

    Example:

        FROM Student AS T1
        JOIN Course T2

    produces:

        {"t1", "t2"}

    This is important because:

        T1.Fname

    is NOT cross-database access.
    It is alias.column.
    """

    aliases: set[str] = set()

    pattern = re.compile(
        rf"""
        \b(?:FROM|JOIN)\s+
        {_identifier_pattern()}
        (?:
            \s+
            (?:AS\s+)?
            (`[^`]+`|[A-Za-z0-9_]+)
        )?
        """,
        flags=re.IGNORECASE | re.VERBOSE,
    )

    for match in pattern.finditer(sql):

        alias = match.group(3)

        if not alias:
            continue

        alias = _clean_identifier(alias)

        if not alias:
            continue

        reserved = {
            "on",
            "where",
            "join",
            "left",
            "right",
            "inner",
            "outer",
            "full",
            "cross",
            "group",
            "order",
            "limit",
            "having",
            "union",
        }

        if alias not in reserved:
            aliases.add(alias)

    return aliases


def _extract_known_tables(sql: str) -> set[str]:
    """
    Extract table names from FROM/JOIN clauses.
    """

    tables: set[str] = set()

    pattern = re.compile(
        rf"""
        \b(?:FROM|JOIN)\s+
        {_identifier_pattern()}
        """,
        flags=re.IGNORECASE | re.VERBOSE,
    )

    for match in pattern.finditer(sql):

        table = (
            match.group(1)
            or match.group(2)
        )

        table = _clean_identifier(table)

        if table:
            tables.add(table)

    return tables


def _qualified_databases(sql: str) -> set[str]:
    """
    Find actual database.table references.

    IMPORTANT:

    MySQL uses:

        database.table

    while normal aliases use:

        T1.column

    The previous implementation treated BOTH as database.table.

    This version first discovers table aliases and ignores
    alias.column references.
    """

    found: set[str] = set()

    aliases = _extract_table_aliases(sql)

    pattern = re.compile(
        rf"""
        {_identifier_pattern()}
        \s*\.\s*
        {_identifier_pattern()}
        """,
        flags=re.IGNORECASE | re.VERBOSE,
    )

    for match in pattern.finditer(sql):

        first = (
            match.group(1)
            or match.group(2)
        )

        if not first:
            continue

        first = _clean_identifier(first)

        # T1.Fname / s.LName / c.CName
        # These are aliases, NOT databases.
        if first in aliases:
            continue

        # Ignore obvious SQL/table aliases.
        if first in {
            "t1",
            "t2",
            "t3",
            "t4",
            "t5",
            "s",
            "c",
            "e",
            "f",
            "d",
            "m",
        }:
            continue

        found.add(first)

    return found


# ============================================================
# INPUT
# ============================================================

def validate_user_question(
    question: str,
) -> GuardrailResult:

    if not isinstance(question, str):
        return GuardrailResult(
            False,
            "Question must be text.",
            "invalid_input",
        )

    question = question.strip()

    if not question:
        return GuardrailResult(
            False,
            "Question cannot be empty.",
            "invalid_input",
        )

    if len(question) > MAX_QUESTION_LENGTH:
        return GuardrailResult(
            False,
            f"Question exceeds the {MAX_QUESTION_LENGTH}-character limit.",
            "input_too_long",
        )

    if "\x00" in question:
        return GuardrailResult(
            False,
            "Null bytes are not allowed.",
            "invalid_input",
        )

    for pattern, category in BLOCKED_INPUT_PATTERNS:

        if re.search(
            pattern,
            question,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            return GuardrailResult(
                False,
                "The request was blocked by the input safety guardrail.",
                category,
            )

    return GuardrailResult(
        True,
        "Input passed guardrails.",
        "ok",
    )


# ============================================================
# SQL
# ============================================================

def validate_sql_guardrail(
    sql: str,
    active_database: str,
) -> GuardrailResult:

    if not isinstance(sql, str) or not sql.strip():
        return GuardrailResult(
            False,
            "Generated SQL is empty.",
            "invalid_sql",
        )

    sql = sql.strip()

    if "\x00" in sql:
        return GuardrailResult(
            False,
            "SQL contains a null byte.",
            "invalid_sql",
        )

    # --------------------------------------------------------
    # ONE STATEMENT ONLY
    # --------------------------------------------------------

    statements = [
        part.strip()
        for part in sql.split(";")
        if part.strip()
    ]

    if len(statements) != 1:
        return GuardrailResult(
            False,
            "Multiple SQL statements are not allowed.",
            "multi_statement",
        )

    # --------------------------------------------------------
    # NO COMMENTS
    # --------------------------------------------------------

    if (
        "--" in sql
        or "/*" in sql
        or "*/" in sql
        or "#" in sql
    ):
        return GuardrailResult(
            False,
            "SQL comments are not allowed.",
            "sql_comment",
        )

    # --------------------------------------------------------
    # FORBIDDEN OPERATIONS
    # --------------------------------------------------------

    forbidden = _contains_forbidden_keyword(sql)

    if forbidden:
        return GuardrailResult(
            False,
            f"Forbidden SQL operation: {forbidden}.",
            "destructive_sql",
        )

    upper = _strip_sql_strings(
        sql.upper()
    )

    # --------------------------------------------------------
    # UNION
    # --------------------------------------------------------

    if re.search(
        r"\bUNION\b",
        upper,
    ):
        return GuardrailResult(
            False,
            "UNION queries are blocked by the SQL guardrail.",
            "union",
        )

    # --------------------------------------------------------
    # INJECTION
    # --------------------------------------------------------

    for pattern in SQL_INJECTION_PATTERNS:

        if re.search(
            pattern,
            sql,
            flags=re.IGNORECASE,
        ):
            return GuardrailResult(
                False,
                "SQL injection pattern detected.",
                "sql_injection",
            )

    # --------------------------------------------------------
    # READ ONLY
    # --------------------------------------------------------

    if not re.match(
        r"^\s*(SELECT|WITH)\b",
        upper,
    ):
        return GuardrailResult(
            False,
            "Only SELECT/WITH read-only queries are allowed.",
            "not_read_only",
        )

    # --------------------------------------------------------
    # ACTIVE DATABASE
    # --------------------------------------------------------

    active = active_database.strip().lower()

    if not re.fullmatch(
        r"[A-Za-z0-9_]+",
        active,
    ):
        return GuardrailResult(
            False,
            "Invalid active database identifier.",
            "invalid_database",
        )

    # --------------------------------------------------------
    # CROSS DATABASE
    # --------------------------------------------------------

    for database in _qualified_databases(sql):

        if database.upper() in SYSTEM_DATABASES:
            return GuardrailResult(
                False,
                "System database access is blocked.",
                "system_database",
            )

        if database != active:
            return GuardrailResult(
                False,
                "Cross-database access is blocked.",
                "cross_database",
            )

    return GuardrailResult(
        True,
        "SQL passed security guardrails.",
        "ok",
    )


# ============================================================
# RESULT / OUTPUT
# ============================================================

def sanitize_result_rows(
    rows: Any,
) -> list[dict[str, Any]]:

    if rows is None:
        return []

    if (
        not isinstance(rows, Iterable)
        or isinstance(rows, (str, bytes, dict))
    ):
        return []

    sanitized: list[dict[str, Any]] = []

    for row in list(rows)[:MAX_RESULT_ROWS]:

        if hasattr(row, "_mapping"):
            row = dict(row._mapping)

        elif not isinstance(row, dict):

            try:
                row = dict(row)

            except Exception:
                continue

        clean: dict[str, Any] = {}

        for key, value in row.items():

            safe_key = str(key)[:200]

            if isinstance(value, bytes):

                value = value.decode(
                    "utf-8",
                    errors="replace",
                )

            elif (
                value is not None
                and not isinstance(
                    value,
                    (str, int, float, bool),
                )
            ):

                value = str(value)

            if (
                isinstance(value, str)
                and len(value)
                > MAX_RESULT_CELL_LENGTH
            ):
                value = (
                    value[:MAX_RESULT_CELL_LENGTH]
                    + "..."
                )

            clean[safe_key] = value

        sanitized.append(clean)

    return sanitized


def validate_output(
    answer: str,
) -> GuardrailResult:

    if not isinstance(answer, str):
        return GuardrailResult(
            False,
            "Answer must be text.",
            "invalid_output",
        )

    if len(answer) > 10000:
        return GuardrailResult(
            False,
            "Answer exceeds the output size limit.",
            "output_too_long",
        )

    secret_patterns = [
        r"(?i)groq[_\s-]*api[_\s-]*key",
        r"(?i)langsmith[_\s-]*api[_\s-]*key",
        r"(?i)mysql[_\s-]*password",
        r"(?i)password\s*=",
        r"(?i)sk-[A-Za-z0-9_-]{20,}",
    ]

    for pattern in secret_patterns:

        if re.search(
            pattern,
            answer,
        ):
            return GuardrailResult(
                False,
                "Potential secret leakage detected.",
                "secret_leak",
            )

    return GuardrailResult(
        True,
        "Output passed guardrails.",
        "ok",
    )


# ============================================================
# CONVENIENCE
# ============================================================

def guard_question_or_raise(
    question: str,
) -> None:

    result = validate_user_question(question)

    if not result.allowed:
        raise ValueError(
            result.reason
        )


def guard_sql_or_raise(
    sql: str,
    active_database: str,
) -> None:

    result = validate_sql_guardrail(
        sql,
        active_database,
    )

    if not result.allowed:
        raise ValueError(
            result.reason
        )


def guard_output_or_raise(
    answer: str,
) -> None:

    result = validate_output(answer)

    if not result.allowed:
        raise ValueError(
            result.reason
        )