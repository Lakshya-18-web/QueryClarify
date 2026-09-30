import argparse
import csv
import json
import os
import re
import time

from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from langchain_groq import ChatGroq
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
DATA_DIR = PROJECT_DIR / "data"
SPIDERMAN_DIR = DATA_DIR / "spiderman"
CHROMA_DIR = DATA_DIR / "chroma"
ENV_FILE = BASE_DIR / ".env"

load_dotenv(ENV_FILE)

MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
)

if not MYSQL_PASSWORD:
    raise ValueError(
        "MYSQL_PASSWORD not found in app/.env"
    )

if not GROQ_API_KEY:
    raise ValueError(
        "GROQ_API_KEY not found in app/.env"
    )


llm = ChatGroq(
    model=GROQ_MODEL,
    groq_api_key=GROQ_API_KEY,
    temperature=0
)

LLM_CALLS = 0

embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)

table_vectorstore = Chroma(
    collection_name="spiderman_schema",
    embedding_function=embeddings,
    persist_directory=str(CHROMA_DIR)
)


def normalize_identifier(value: str) -> str:
    return value.strip().replace("`", "").lower()


def extract_text(response) -> str:
    content = response.content

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []

        for item in content:
            if isinstance(item, str):
                parts.append(item)

            elif isinstance(item, dict) and "text" in item:
                parts.append(str(item["text"]))

        return "".join(parts).strip()

    return str(content).strip()


def clean_sql(sql: str) -> str:
    sql = sql.strip()

    sql = re.sub(
        r"```sql",
        "",
        sql,
        flags=re.IGNORECASE
    )

    sql = re.sub(
        r"```",
        "",
        sql
    )

    sql = sql.strip()

    if sql.endswith(";"):
        return sql

    return sql + ";"


def get_database_engine(database: str):
    database = normalize_identifier(database)

    if not re.fullmatch(r"[a-zA-Z0-9_]+", database):
        raise ValueError(f"Unsafe database name: {database}")

    return create_engine(
        f"mysql+pymysql://root:{MYSQL_PASSWORD}@localhost:3306/{database}"
    )


def get_database_schema(database: str) -> str:
    engine = get_database_engine(database)
    inspector = inspect(engine)

    schema_parts = []

    for table in inspector.get_table_names():
        columns = inspector.get_columns(table)
        pk = inspector.get_pk_constraint(table)
        fks = inspector.get_foreign_keys(table)

        lines = [
            f"TABLE: {table}",
            "COLUMNS:"
        ]

        for column in columns:
            lines.append(
                f"- {column['name']} ({column['type']})"
            )

        pk_columns = pk.get("constrained_columns", [])

        if pk_columns:
            lines.append(
                "PRIMARY KEYS: "
                + ", ".join(pk_columns)
            )

        if fks:
            lines.append("FOREIGN KEYS:")

            for fk in fks:
                constrained = fk.get(
                    "constrained_columns",
                    []
                )
                referred_table = fk.get(
                    "referred_table"
                )
                referred_columns = fk.get(
                    "referred_columns",
                    []
                )

                lines.append(
                    f"- {', '.join(constrained)} -> "
                    f"{referred_table}"
                    f"({', '.join(referred_columns)})"
                )

        schema_parts.append(
            "\n".join(lines)
        )

    return "\n\n".join(schema_parts)


def get_gold_columns(row: dict[str, str]):
    lowered = {
        key.strip().lower(): key
        for key in row
    }

    database_keys = [
        "database",
        "db_id",
        "db",
        "database_id"
    ]

    question_keys = [
        "question",
        "natural_language",
        "nl_question"
    ]

    sql_keys = [
        "query",
        "sql",
        "gold_sql",
        "gold_query"
    ]

    database_key = next(
        (
            lowered[key]
            for key in database_keys
            if key in lowered
        ),
        None
    )

    question_key = next(
        (
            lowered[key]
            for key in question_keys
            if key in lowered
        ),
        None
    )

    sql_key = next(
        (
            lowered[key]
            for key in sql_keys
            if key in lowered
        ),
        None
    )

    if not database_key:
        raise ValueError(
            "Could not find database column. "
            "Expected one of: "
            + ", ".join(database_keys)
        )

    if not question_key:
        raise ValueError(
            "Could not find question column. "
            "Expected one of: "
            + ", ".join(question_keys)
        )

    if not sql_key:
        raise ValueError(
            "Could not find gold SQL column. "
            "Expected one of: "
            + ", ".join(sql_keys)
        )

    return database_key, question_key, sql_key


def load_test_queries(path: Path):
    if not path.exists():
        raise FileNotFoundError(
            f"Test file not found: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:
        reader = csv.DictReader(file)
        rows = list(reader)

    if not rows:
        raise ValueError(
            f"No rows found in {path}"
        )

    database_key, question_key, sql_key = (
        get_gold_columns(rows[0])
    )

    records = []

    for index, row in enumerate(rows):
        database = (
            row.get(database_key, "")
            .strip()
        )
        question = (
            row.get(question_key, "")
            .strip()
        )
        gold_sql = (
            row.get(sql_key, "")
            .strip()
        )

        if not database or not question or not gold_sql:
            continue

        records.append(
            {
                "index": index,
                "database": database,
                "question": question,
                "gold_sql": clean_sql(gold_sql)
            }
        )

    return records


def extract_table_name(document: str, metadata: dict) -> str:
    if metadata:
        if metadata.get("table"):
            return str(metadata["table"])

    match = re.search(
        r"TABLE:\s*([A-Za-z0-9_]+)",
        document,
        flags=re.IGNORECASE
    )

    if match:
        return match.group(1)

    return ""


def retrieve_rag_schema(
    question: str,
    database: str,
    k: int = 5
) -> tuple[str, list[str]]:
    results = table_vectorstore.similarity_search(
        question,
        k=k,
        filter={
            "database": database
        }
    )

    documents = []
    tables = []

    for result in results:
        document = result.page_content
        metadata = result.metadata or {}

        table = extract_table_name(
            document,
            metadata
        )

        if table:
            tables.append(table)
            documents.append(document)

    return (
        "\n\n".join(documents),
        tables
    )



class ClarificationDecision(BaseModel):
    clear: bool = Field(description="True when the question is sufficiently specific to answer.")
    clarification: str = Field(description="A concise clarification question if the query is ambiguous; otherwise an empty string.")


def is_likely_clear(question: str) -> bool:
    q = re.sub(r"\s+", " ", question.strip().lower())
    if not q:
        return False
    words = q.split()
    if len(words) <= 4:
        ambiguous_patterns = [
            r"^(show|give|list|display)\s+(me\s+)?the\s+\w+s?$",
            r"^(what|how)\s+about\s+the\s+\w+s?$",
            r"^(show|give|list|display)\s+\w+s?$",
        ]
        if any(re.search(pattern, q) for pattern in ambiguous_patterns):
            return False
    return True


def assess_clarity(question: str) -> ClarificationDecision:
    prompt = f"""
You are a query-understanding component for a Text-to-SQL system.

Determine whether the user's question is specific enough to generate SQL without asking a follow-up.

A question is AMBIGUOUS when important information is genuinely missing, such as:
- which entity/table is intended when multiple interpretations are plausible
- which attribute/filter is intended
- which database-level concept is intended

A question is CLEAR when the requested operation and target are sufficiently specified,
even if the answer requires joins, aggregation, filtering, sorting, or multiple tables.

Do not ask for information that can be inferred from the schema and question.

Return structured output.

USER QUESTION:
{question}
"""
    global LLM_CALLS
    LLM_CALLS += 1

    try:
        structured_llm = llm.with_structured_output(ClarificationDecision)
        return structured_llm.invoke(prompt)
    except Exception as error:
        message = str(error)

        if (
            "RESOURCE_EXHAUSTED" in message
            or "429" in message
            or "quota" in message.lower()
        ):
            raise RuntimeError(
                "QUOTA_FAILURE: Groq API quota exhausted."
            ) from error

        if "503" in message or "UNAVAILABLE" in message.upper():
            raise RuntimeError(
                "SERVICE_UNAVAILABLE: Groq model temporarily unavailable."
            ) from error

        raise


def generate_clarified_question(question: str, clarification: str) -> str:
    prompt = f"""
Rewrite the user's original question into one precise Text-to-SQL question
using the clarification below.

Do not add facts that were not provided.
Return ONLY the rewritten question.

ORIGINAL QUESTION:
{question}

CLARIFICATION:
{clarification}
"""
    global LLM_CALLS
    LLM_CALLS += 1

    try:
        response = llm.invoke(prompt)
        return extract_text(response).strip()
    except Exception as error:
        message = str(error)

        if (
            "RESOURCE_EXHAUSTED" in message
            or "429" in message
            or "quota" in message.lower()
        ):
            raise RuntimeError(
                "QUOTA_FAILURE: Groq API quota exhausted."
            ) from error

        if "503" in message or "UNAVAILABLE" in message.upper():
            raise RuntimeError(
                "SERVICE_UNAVAILABLE: Groq model temporarily unavailable."
            ) from error

        raise


def generate_sql(
    question: str,
    database: str,
    schema: str,
    mode: str
) -> str:
    if mode in {"baseline", "clarification"}:
        instruction = """
You are a MySQL Text-to-SQL system.

Generate ONE read-only SQL query that answers
the user's question using the complete database
schema provided below.

Rules:
1. Use ONLY tables and columns in the schema.
2. Use MySQL syntax.
3. Follow foreign-key relationships.
4. Do not invent schema elements.
5. The query must be read-only.
6. Return ONLY SQL.
"""
    else:
        instruction = """
You are a MySQL Text-to-SQL system using schema RAG.

Generate ONE read-only SQL query that answers
the user's question using ONLY the retrieved schema.

Rules:
1. Use ONLY retrieved tables and columns.
2. Use exact table and column names.
3. Follow foreign-key relationships shown in the schema.
4. Do not invent schema elements.
5. Use MySQL syntax.
6. The query must be read-only.
7. Return ONLY SQL.
"""

    prompt = f"""
{instruction}

DATABASE:
{database}

USER QUESTION:
{question}

SCHEMA:
{schema}

SQL:
"""

    global LLM_CALLS
    LLM_CALLS += 1

    try:
        response = llm.invoke(prompt)
    except Exception as error:
        message = str(error)

        if (
            "RESOURCE_EXHAUSTED" in message
            or "429" in message
            or "quota" in message.lower()
        ):
            raise RuntimeError(
                "QUOTA_FAILURE: Groq API quota exhausted."
            ) from error

        if "503" in message or "UNAVAILABLE" in message.upper():
            raise RuntimeError(
                "SERVICE_UNAVAILABLE: Groq model temporarily unavailable."
            ) from error

        raise

    return clean_sql(
        extract_text(response)
    )


def extract_sql_tables(sql: str) -> list[str]:
    patterns = [
        r"\bFROM\s+([A-Za-z0-9_`.]+)",
        r"\bJOIN\s+([A-Za-z0-9_`.]+)"
    ]

    tables = []

    for pattern in patterns:
        matches = re.findall(
            pattern,
            sql,
            flags=re.IGNORECASE
        )

        tables.extend(matches)

    cleaned = []

    for table in tables:
        table = table.strip("`")

        if "." in table:
            table = table.split(".")[-1]

        cleaned.append(
            normalize_identifier(table)
        )

    return cleaned


def validate_read_only_sql(
    sql: str,
    database: str
) -> tuple[bool, str]:
    forbidden = [
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "CREATE",
        "TRUNCATE",
        "REPLACE"
    ]

    sql_upper = sql.upper()

    for keyword in forbidden:
        if re.search(
            rf"\b{keyword}\b",
            sql_upper
        ):
            return (
                False,
                f"Forbidden SQL operation: {keyword}"
            )

    try:
        engine = get_database_engine(database)
        inspector = inspect(engine)

        actual_tables = {
            normalize_identifier(table)
            for table in inspector.get_table_names()
        }

        sql_tables = extract_sql_tables(sql)

        missing = [
            table
            for table in sql_tables
            if table not in actual_tables
        ]

        if missing:
            return (
                False,
                "Tables not present in database: "
                + ", ".join(sorted(set(missing)))
            )

        return (
            True,
            "SQL passed basic validation."
        )

    except Exception as error:
        return (
            False,
            f"Validation error: {error}"
        )


def execute_sql(
    database: str,
    sql: str
) -> tuple[Any, str]:
    try:
        engine = get_database_engine(database)

        with engine.connect() as connection:
            result = connection.execute(
                text(sql)
            )

            rows = result.mappings().all()

        output = [
            dict(row)
            for row in rows
        ]

        return output, ""

    except SQLAlchemyError as error:
        return None, str(error)

    except Exception as error:
        return None, str(error)


def normalize_value(value):
    if value is None:
        return None

    if isinstance(value, bytes):
        return value.decode(
            "utf-8",
            errors="replace"
        )

    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass

    return str(value)


def normalize_rows(rows):
    if rows is None:
        return None

    normalized = []

    for row in rows:
        if isinstance(row, dict):
            values = tuple(
                normalize_value(value)
                for value in row.values()
            )
        else:
            values = tuple(
                normalize_value(value)
                for value in row
            )

        normalized.append(values)

    return normalized


def has_order_by(sql: str) -> bool:
    return bool(
        re.search(
            r"\bORDER\s+BY\b",
            sql,
            flags=re.IGNORECASE
        )
    )


def results_match(
    predicted,
    gold,
    predicted_sql,
    gold_sql
) -> bool:
    if predicted is None or gold is None:
        return False

    predicted_rows = normalize_rows(
        predicted
    )
    gold_rows = normalize_rows(
        gold
    )

    if (
        has_order_by(predicted_sql)
        or has_order_by(gold_sql)
    ):
        return predicted_rows == gold_rows

    return sorted(predicted_rows) == sorted(
        gold_rows
    )


def normalize_sql(sql: str) -> str:
    sql = clean_sql(sql)

    sql = sql.lower()

    sql = re.sub(
        r"\s+",
        " ",
        sql
    )

    sql = sql.replace(
        " ;",
        ";"
    )

    return sql.strip()


def run_one(
    record: dict,
    mode: str,
    top_k: int
) -> dict:
    database = record["database"]
    question = record["question"]
    gold_sql = record["gold_sql"]

    global LLM_CALLS
    calls_before = LLM_CALLS
    start = time.perf_counter()

    result = {
        "index": record["index"],
        "database": database,
        "question": question,
        "gold_sql": gold_sql,
        "predicted_sql": "",
        "gold_result": None,
        "predicted_result": None,
        "sql_exact_match": False,
        "execution_correct": False,
        "validation_valid": False,
        "validation_message": "",
        "execution_error": "",
        "rag_tables": "",
        "clarification_clear": "",
        "clarification_question": "",
        "latency_seconds": 0.0,
        "llm_calls": 0,
        "status": "failed"
    }

    try:
        gold_result, gold_error = execute_sql(
            database,
            gold_sql
        )

        if gold_error:
            result["status"] = "gold_execution_failed"
            result["execution_error"] = (
                "Gold SQL error: "
                + gold_error
            )
            return result

        result["gold_result"] = json.dumps(
            gold_result,
            default=str
        )

        working_question = question

        if mode == "baseline":
            schema = get_database_schema(
                database
            )
            rag_tables = []

        elif mode in {"rag", "clarification"}:
            if mode == "clarification":
                clear = is_likely_clear(question)
                result["clarification_clear"] = clear
                result["clarification_question"] = ""

                if not clear:
                    decision = assess_clarity(question)
                    result["clarification_clear"] = decision.clear
                    result["clarification_question"] = decision.clarification

                    if not decision.clear:
                        working_question = generate_clarified_question(
                            question,
                            decision.clarification
                        )

            schema, rag_tables = (
                retrieve_rag_schema(
                    working_question,
                    database,
                    top_k
                )
            )

            if not schema:
                result["status"] = (
                    "rag_retrieval_failed"
                )
                return result

        else:
            raise ValueError(
                f"Unsupported mode: {mode}"
            )

        result["rag_tables"] = "|".join(
            rag_tables
        )

        predicted_sql = generate_sql(
            working_question,
            database,
            schema,
            mode
        )

        result["predicted_sql"] = (
            predicted_sql
        )

        result["sql_exact_match"] = (
            normalize_sql(predicted_sql)
            == normalize_sql(gold_sql)
        )

        valid, message = (
            validate_read_only_sql(
                predicted_sql,
                database
            )
        )

        result["validation_valid"] = valid
        result["validation_message"] = message

        if not valid:
            result["status"] = (
                "validation_failed"
            )
            return result

        predicted_result, execution_error = (
            execute_sql(
                database,
                predicted_sql
            )
        )

        if execution_error:
            result["execution_error"] = (
                execution_error
            )
            result["status"] = (
                "execution_failed"
            )
            return result

        result["predicted_result"] = (
            json.dumps(
                predicted_result,
                default=str
            )
        )

        result["execution_correct"] = (
            results_match(
                predicted_result,
                gold_result,
                predicted_sql,
                gold_sql
            )
        )

        result["status"] = (
            "correct"
            if result["execution_correct"]
            else "incorrect"
        )

        return result

    except Exception as error:
        message = str(error)

        if (
            "QUOTA_FAILURE" in message
            or "RESOURCE_EXHAUSTED" in message
            or "429" in message
            or "quota" in message.lower()
        ):
            result["status"] = "quota_failure"
        elif "SERVICE_UNAVAILABLE" in message:
            result["status"] = "service_unavailable"
        elif "503" in message or "UNAVAILABLE" in message.upper():
            result["status"] = "service_unavailable"
        else:
            result["status"] = "error"

        result["execution_error"] = message
        return result

    finally:
        result["llm_calls"] = LLM_CALLS - calls_before
        result["latency_seconds"] = round(
            time.perf_counter() - start,
            3
        )


def save_results(
    results: list[dict],
    output_path: Path
):
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    fieldnames = [
        "index",
        "database",
        "question",
        "gold_sql",
        "predicted_sql",
        "gold_result",
        "predicted_result",
        "sql_exact_match",
        "execution_correct",
        "validation_valid",
        "validation_message",
        "execution_error",
        "rag_tables",
        "clarification_clear",
        "clarification_question",
        "latency_seconds",
        "llm_calls",
        "status"
    ]

    with output_path.open(
        "w",
        encoding="utf-8",
        newline=""
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames
        )

        writer.writeheader()

        writer.writerows(results)


def print_summary(
    results: list[dict],
    mode: str
):
    total = len(results)

    exact = sum(
        str(row["sql_exact_match"]).lower() == "true"
        for row in results
    )

    execution = sum(
        str(row["execution_correct"]).lower() == "true"
        for row in results
    )

    valid = sum(
        str(row["validation_valid"]).lower() == "true"
        for row in results
    )

    gold_failed = sum(
        row["status"]
        == "gold_execution_failed"
        for row in results
    )

    quota_failed = sum(
        row["status"] == "quota_failure"
        for row in results
    )

    service_failed = sum(
        row["status"] == "service_unavailable"
        for row in results
    )

    prediction_failed = sum(
        row["status"]
        in {
            "validation_failed",
            "execution_failed",
            "error",
            "rag_retrieval_failed"
        }
        for row in results
    )

    total_llm_calls = sum(
        int(row["llm_calls"] or 0)
        for row in results
    )

    avg_latency = (
        sum(
            float(row["latency_seconds"] or 0)
            for row in results
        )
        / total
        if total
        else 0
    )

    print()
    print("=" * 60)
    print("BENCHMARK SUMMARY")
    print("=" * 60)

    print(f"Mode:                  {mode}")
    print(f"Total examples:        {total}")
    print(
        f"SQL exact match:       "
        f"{exact}/{total} "
        f"({exact / total * 100:.2f}%)"
        if total
        else "SQL exact match:       0/0"
    )
    print(
        f"Execution accuracy:    "
        f"{execution}/{total} "
        f"({execution / total * 100:.2f}%)"
        if total
        else "Execution accuracy:    0/0"
    )
    print(
        f"Validation pass rate:  "
        f"{valid}/{total} "
        f"({valid / total * 100:.2f}%)"
        if total
        else "Validation pass rate:  0/0"
    )
    print(
        f"Prediction failures:   {prediction_failed}"
    )
    print(
        f"Quota failures:        {quota_failed}"
    )
    print(
        f"Service unavailable:   {service_failed}"
    )
    print(
        f"Gold SQL failures:     {gold_failed}"
    )
    print(
        f"Average latency:       {avg_latency:.3f}s"
    )
    print(
        f"Total LLM calls:       {total_llm_calls}"
    )

    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "QueryClarify SpiderMan benchmark runner"
        )
    )

    parser.add_argument(
        "--mode",
        choices=[
            "baseline",
            "rag",
            "clarification"
        ],
        default="rag"
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help=(
            "Number of test examples to run. "
            "Use 0 for all."
        )
    )

    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help=(
            "Number of RAG tables to retrieve."
        )
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Resume from an existing results CSV. "
            "Completed indexes are skipped."
        )
    )

    parser.add_argument(
        "--input",
        type=str,
        default=str(
            SPIDERMAN_DIR / "test_queries.csv"
        )
    )

    parser.add_argument(
        "--output",
        type=str,
        default=""
    )

    args = parser.parse_args()

    input_path = Path(args.input)

    records = load_test_queries(
        input_path
    )

    if args.limit > 0:
        records = records[:args.limit]

    if not records:
        raise ValueError(
            "No benchmark records selected."
        )

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = (
            DATA_DIR
            / "evaluation"
            / f"{args.mode}_results.csv"
        )

    print("=" * 60)
    print("QUERYCLARIFY BENCHMARK")
    print("=" * 60)
    print(f"Input:       {input_path}")
    print(f"Mode:        {args.mode}")
    print(f"Examples:    {len(records)}")
    print(f"RAG top-k:   {args.top_k}")
    print(f"Output:      {output_path}")
    print("=" * 60)

    results = []

    if args.resume and output_path.exists():
        print()
        print("RESUME MODE")

        try:
            with output_path.open(
                "r",
                encoding="utf-8",
                newline=""
            ) as file:
                reader = csv.DictReader(file)
                existing = list(reader)

            completed = {}

            for row in existing:
                try:
                    index = int(row["index"])
                except (ValueError, TypeError, KeyError):
                    continue

                completed[index] = row

            results = [
                completed[record["index"]]
                for record in records
                if record["index"] in completed
            ]

            completed_indexes = set(completed)

            print(
                f"Existing results: {len(completed_indexes)}"
            )
            print(
                f"Remaining examples: "
                f"{len([r for r in records if r['index'] not in completed_indexes])}"
            )

        except Exception as error:
            print(
                f"Could not resume from existing results: {error}"
            )
            print("Starting fresh.")
            results = []
            completed_indexes = set()
    else:
        completed_indexes = set()

    total_records = len(records)

    for record in records:

        if record["index"] in completed_indexes:
            continue

        completed_count = len(results) + 1

        print()
        print(
            f"[{completed_count}/{total_records}] "
            f"{record['database']}"
        )
        print(
            f"Question: {record['question']}"
        )

        result = run_one(
            record,
            args.mode,
            args.top_k
        )

        results.append(result)

        save_results(
            results,
            output_path
        )

        if result["status"] == "quota_failure":
            print("Quota exhausted. Stopping benchmark.")
            break

        print(
            f"Status: {result['status']}"
        )

        print(
            "Execution correct: "
            f"{result['execution_correct']}"
        )

        print(
            "SQL exact match: "
            f"{result['sql_exact_match']}"
        )

        if result["rag_tables"]:
            print(
                "RAG tables: "
                f"{result['rag_tables']}"
            )

        if result.get("clarification_clear") != "":
            print(
                "Clarification clear: "
                f"{result['clarification_clear']}"
            )

        if result.get("clarification_question"):
            print(
                "Clarification: "
                f"{result['clarification_question']}"
            )

        if result["execution_error"]:
            print(
                "Error: "
                f"{result['execution_error']}"
            )

        print(
            f"Latency: "
            f"{result['latency_seconds']:.3f}s"
        )

    save_results(
        results,
        output_path
    )

    print_summary(
        results,
        args.mode
    )

    print(
        f"\nResults saved to:\n{output_path}"
    )


if __name__ == "__main__":
    main()
