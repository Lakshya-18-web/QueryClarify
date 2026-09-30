import os
import re
from typing import TypedDict, Optional

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

from langgraph.graph import StateGraph, START, END

from app.guardrails import (
    validate_user_question,
    validate_sql_guardrail,
    sanitize_result_rows,
    validate_output,
)
from app.langsmith_tracing import traced_node, tracing_status


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, ".env")
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "data"))
CHROMA_DIR = os.path.join(DATA_DIR, "chroma")

load_dotenv(ENV_FILE)

MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

if not MYSQL_PASSWORD:
    raise ValueError("MYSQL_PASSWORD not found in .env")

if not GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY not found in .env")


llm = ChatGoogleGenerativeAI(
    model="gemini-3.6-flash",
    google_api_key=GOOGLE_API_KEY
)


embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)


vectorstore = Chroma(
    collection_name="spiderman_schema",
    embedding_function=embeddings,
    persist_directory=CHROMA_DIR
)


database_vectorstore = Chroma(
    collection_name="spiderman_databases",
    embedding_function=embeddings,
    persist_directory=CHROMA_DIR
)


class QueryState(TypedDict, total=False):
    question: str

    clear: bool
    clarification_question: str
    user_clarification: str

    database: str
    database_clarification_question: str
    user_database_clarification: str
    database_candidates: list
    schema: str

    sql: str

    execution_error: str
    result: Optional[list]

    validation_valid: bool
    validation_message: str

    retry_count: int


def extract_text(response):
    content = response.content

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []

        for item in content:
            if isinstance(item, str):
                parts.append(item)

            elif isinstance(item, dict):
                if "text" in item:
                    parts.append(str(item["text"]))

        return "\n".join(parts)

    return str(content)


def clean_sql(sql):
    sql = sql.strip()

    sql = re.sub(r"```sql", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"```", "", sql)

    sql = sql.strip()

    if sql.endswith(";"):
        return sql

    return sql + ";"


def normalize_identifier(name):
    name = name.strip()
    name = name.replace("`", "")
    return name.lower()


def get_database_engine(database):
    database = normalize_identifier(database)

    if not re.fullmatch(r"[a-zA-Z0-9_]+", database):
        raise ValueError(f"Unsafe database name: {database}")

    return create_engine(
        f"mysql+pymysql://root:{MYSQL_PASSWORD}@localhost:3306/{database}"
    )


def get_database_tables(database):
    engine = get_database_engine(database)

    inspector = inspect(engine)

    return inspector.get_table_names()


def get_table_schema(database, table):
    engine = get_database_engine(database)

    inspector = inspect(engine)

    actual_tables = inspector.get_table_names()

    actual_map = {
        normalize_identifier(t): t
        for t in actual_tables
    }

    normalized_table = normalize_identifier(table)

    if normalized_table not in actual_map:
        return ""

    actual_table = actual_map[normalized_table]

    columns = inspector.get_columns(actual_table)

    primary_key = inspector.get_pk_constraint(actual_table)

    foreign_keys = inspector.get_foreign_keys(actual_table)

    lines = []

    lines.append(f"TABLE: {actual_table}")

    lines.append("COLUMNS:")

    for column in columns:
        name = column["name"]
        datatype = str(column["type"])

        lines.append(
            f"- {name} ({datatype})"
        )

    pk_columns = primary_key.get("constrained_columns", [])

    if pk_columns:
        lines.append(
            "PRIMARY KEYS: " + ", ".join(pk_columns)
        )

    if foreign_keys:
        lines.append("FOREIGN KEYS:")

        for fk in foreign_keys:
            constrained = fk.get("constrained_columns", [])
            referred_table = fk.get("referred_table")
            referred_columns = fk.get("referred_columns", [])

            lines.append(
                f"- {', '.join(constrained)} -> "
                f"{referred_table}({', '.join(referred_columns)})"
            )

    return "\n".join(lines)


def get_foreign_keys(database, table):
    engine = get_database_engine(database)

    inspector = inspect(engine)

    actual_tables = inspector.get_table_names()

    actual_map = {
        normalize_identifier(t): t
        for t in actual_tables
    }

    normalized_table = normalize_identifier(table)

    if normalized_table not in actual_map:
        return []

    actual_table = actual_map[normalized_table]

    foreign_keys = inspector.get_foreign_keys(actual_table)

    return foreign_keys


def get_database_schema(database):
    tables = get_database_tables(database)

    schema_parts = []

    for table in tables:
        schema = get_table_schema(database, table)

        if schema:
            schema_parts.append(schema)

    return "\n\n".join(schema_parts)


def get_sample_values(database, table):
    engine = get_database_engine(database)

    inspector = inspect(engine)

    actual_tables = inspector.get_table_names()

    table_map = {
        normalize_identifier(t): t
        for t in actual_tables
    }

    normalized_table = normalize_identifier(table)

    if normalized_table not in table_map:
        return {}

    actual_table = table_map[normalized_table]

    columns = inspector.get_columns(actual_table)

    values = {}

    with engine.connect() as connection:

        for column in columns:

            column_name = column["name"]

            try:
                query = text(
                    f"""
                    SELECT `{column_name}`
                    FROM `{actual_table}`
                    WHERE `{column_name}` IS NOT NULL
                    LIMIT 5
                    """
                )

                rows = connection.execute(query).fetchall()

                values[column_name] = [
                    str(row[0])
                    for row in rows
                ]

            except Exception:
                values[column_name] = []

    return values


def tokenize(text_value):
    return set(
        re.findall(
            r"[a-zA-Z0-9_]+",
            text_value.lower()
        )
    )


def get_table_name(metadata, document_text=""):
    if metadata:

        if metadata.get("table"):
            return str(metadata["table"])

        if metadata.get("name"):
            return str(metadata["name"])

    match = re.search(
        r"TABLE\s*:\s*([A-Za-z0-9_]+)",
        document_text,
        flags=re.IGNORECASE
    )

    if match:
        return match.group(1)

    match = re.search(
        r"table\s*=\s*([A-Za-z0-9_]+)",
        document_text,
        flags=re.IGNORECASE
    )

    if match:
        return match.group(1)

    return ""


def score_table(question, document, metadata):
    question_lower = question.lower()

    table_name = get_table_name(
        metadata,
        document
    )

    if not table_name:
        return 0

    score = 0

    question_tokens = tokenize(question)

    table_tokens = tokenize(table_name)

    schema_tokens = tokenize(document)

    table_overlap = question_tokens.intersection(
        table_tokens
    )

    schema_overlap = question_tokens.intersection(
        schema_tokens
    )

    score += len(table_overlap) * 15

    score += len(schema_overlap)

    sample_values = []

    for match in re.findall(
        r"(?:SAMPLE VALUES|SAMPLE VALUE|VALUES)\s*:",
        document,
        flags=re.IGNORECASE
    ):
        pass

    lines = document.splitlines()

    for line in lines:

        if ":" not in line:
            continue

        left, right = line.split(":", 1)

        if left.strip().startswith("-"):
            value = right.strip()

            if value:
                sample_values.append(value.lower())

    for value in sample_values:

        value = value.strip()

        if len(value) < 3:
            continue

        if value in question_lower:

            if len(value.split()) >= 2:
                score += 20
            else:
                score += 3

    table_lower = table_name.lower()

    if "student" in question_lower:

        if "student" in table_lower:
            score += 15

    if (
        "activity" in question_lower
        or "activities" in question_lower
    ):

        if "activity" in table_lower:
            score += 15

    if (
        "participat" in question_lower
        or "join" in question_lower
    ):

        if "participat" in table_lower:
            score += 15

    return score


def score_database(question, document):
    question_tokens = tokenize(question)

    score = 0

    schema_tokens = tokenize(document)

    overlap = question_tokens.intersection(
        schema_tokens
    )

    score += len(overlap)

    question_lower = question.lower()
    document_lower = document.lower()

    multiword_phrases = [
        phrase.strip()
        for phrase in re.findall(
            r"[a-zA-Z]+(?:\s+[a-zA-Z]+)+",
            question_lower
        )
    ]

    for phrase in multiword_phrases:

        if len(phrase) < 5:
            continue

        if phrase in document_lower:
            score += 15

    if "student" in question_lower and "student" in document_lower:
        score += 2

    if "course" in question_lower and "course" in document_lower:
        score += 2

    if "faculty" in question_lower and "faculty" in document_lower:
        score += 2

    if "school" in question_lower and "school" in document_lower:
        score += 2

    if "player" in question_lower and "player" in document_lower:
        score += 2

    if "activity" in question_lower and "activity" in document_lower:
        score += 2

    return score


@traced_node("database_router_node")
def database_router_node(state: QueryState):
    question = state["question"]

    results = database_vectorstore.get(
        include=["documents", "metadatas"]
    )

    documents = results.get("documents") or []
    metadatas = results.get("metadatas") or []

    scored = []

    for document, metadata in zip(documents, metadatas):
        database = metadata.get("database") if metadata else None

        if not database:
            continue

        score = score_database(
            question,
            document or ""
        )

        scored.append((score, database))

    # Chroma can occasionally return an empty get() result even though
    # the collection exists. Fall back to vector retrieval so a generic
    # question never crashes the graph at the routing stage.
    if not scored:
        fallback_results = database_vectorstore.similarity_search(
            question,
            k=157
        )

        for document in fallback_results:
            metadata = document.metadata or {}
            database = metadata.get("database")

            if not database:
                continue

            score = score_database(
                question,
                document.page_content or ""
            )

            scored.append((score, database))

    if not scored:
        # Last-resort recovery: use the database collection metadata.
        # This makes a generic question become a clarification request
        # instead of terminating the LangGraph execution.
        metadata_result = database_vectorstore.get(
            include=["metadatas"]
        )

        fallback_metadatas = metadata_result.get("metadatas") or []

        for metadata in fallback_metadatas:
            database = metadata.get("database") if metadata else None

            if database:
                scored.append((0, database))

    if not scored:
        raise ValueError("No database candidates found.")

    scored.sort(
        key=lambda x: (-x[0], x[1].lower())
    )

    unique_scored = []
    seen = set()

    for score, database in scored:
        database_norm = normalize_identifier(database)

        if database_norm in seen:
            continue

        seen.add(database_norm)
        unique_scored.append((score, database))

    best_score, best_database = unique_scored[0]
    second_score = (
        unique_scored[1][0]
        if len(unique_scored) > 1
        else 0
    )

    top_candidates = unique_scored[:5]

    print("\nDATABASE ROUTING")
    print("----------------")
    print(f"Databases evaluated: {len(unique_scored)}")
    print(f"Best candidate: {best_database}")
    print(f"Best score: {best_score}")
    print(f"Second score: {second_score}")

    print("\nTOP DATABASE CANDIDATES")
    print("-----------------------")

    for score, database in top_candidates:
        print(f"{database}: {score}")

    # A database is considered confidently selected when the best score
    # is positive and clearly separates itself from the runner-up.
    # Generic questions such as "How many students are there?" can match
    # many databases, so we must ask the user instead of silently choosing
    # an arbitrary database.
    confident = (
        best_score > 0
        and (
            len(unique_scored) == 1
            or best_score > second_score
        )
    )

    if confident:
        return {
            "database": best_database,
            "database_candidates": [
                database
                for _, database in top_candidates
            ],
            "database_clarification_question": "",
            "user_database_clarification": ""
        }

    candidate_names = [
        database
        for _, database in top_candidates
    ]

    clarification_question = (
        "Which database should I query? Available likely matches are: "
        + ", ".join(candidate_names)
        + "."
    )

    print("\nDATABASE AMBIGUITY")
    print("-----------------")
    print(clarification_question)

    return {
        "database": "",
        "database_candidates": candidate_names,
        "database_clarification_question": clarification_question
    }


@traced_node("database_clarification_node")
def database_clarification_node(state: QueryState):
    question = state.get(
        "database_clarification_question",
        ""
    )

    print("\nDATABASE CLARIFICATION")
    print("----------------------")
    print(question)

    answer = input("\nYour database: ").strip()

    return {
        "user_database_clarification": answer
    }


@traced_node("database_selection_node")
def database_selection_node(state: QueryState):
    answer = state.get(
        "user_database_clarification",
        ""
    ).strip()

    candidates = state.get(
        "database_candidates",
        []
    )

    if not answer:
        raise ValueError("Database selection cannot be empty.")

    answer_norm = normalize_identifier(answer)

    candidate_map = {
        normalize_identifier(database): database
        for database in candidates
    }

    if answer_norm in candidate_map:
        database = candidate_map[answer_norm]
    else:
        # Accept an exact database name even if it was not in the top five.
        # The name is still validated through the MySQL inspector below.
        database = answer_norm

    actual_tables = get_database_tables(database)

    if not actual_tables:
        raise ValueError(
            f"Database '{database}' does not exist or contains no tables."
        )

    print("\nDATABASE SELECTED BY USER")
    print("-------------------------")
    print(database)

    return {
        "database": database
    }


def after_database_router(state: QueryState):
    if state.get("database"):
        return "rag"

    return "database_clarification"

@traced_node("rag_node")
def rag_node(state: QueryState):

    question = state["question"]
    database = state["database"]

    # ========================================================
    # GET RAG DOCUMENTS
    # ========================================================

    results = vectorstore.get(
        where={
            "database": database
        }
    )

    documents = results.get(
        "documents",
        []
    )

    metadatas = results.get(
        "metadatas",
        []
    )

    print("\nRAG TABLE EXTRACTION")
    print("--------------------")
    print(f"Database: {database}")

    # ========================================================
    # BUILD TABLE CANDIDATES
    # ========================================================

    table_candidates = []

    for document, metadata in zip(
        documents,
        metadatas
    ):

        table = get_table_name(
            metadata,
            document
        )

        if not table:
            continue

        table_candidates.append(
            (
                table,
                document,
                metadata
            )
        )

    print(
        "Tables found:",
        [
            item[0]
            for item in table_candidates
        ]
    )

    # ========================================================
    # SCORE TABLES
    # ========================================================

    scored_tables = []

    for (
        table,
        document,
        metadata
    ) in table_candidates:

        score = score_table(
            question,
            document,
            metadata
        )

        scored_tables.append(
            (
                score,
                table,
                document,
                metadata
            )
        )

    scored_tables.sort(
        key=lambda item: item[0],
        reverse=True
    )

    print("\nTABLE SCORES")
    print("--------------------")

    for (
        score,
        table,
        _,
        _
    ) in scored_tables:

        print(
            f"{table}: {score}"
        )

    # ========================================================
    # DIRECT TABLE SELECTION
    #
    # Only positively-scored tables are initially selected.
    # We later expand this set using real FK relationships.
    # ========================================================

    direct_selected = [
        table
        for (
            score,
            table,
            _,
            _
        ) in scored_tables
        if score > 0
    ]

    print("\nDIRECTLY SELECTED:")
    print(direct_selected)

    if direct_selected:

        selected_tables = direct_selected[:5]

    else:

        selected_tables = [
            table
            for (
                _,
                table,
                _,
                _
            ) in scored_tables[:3]
        ]

    # ========================================================
    # NORMALIZED SELECTED TABLE NAMES
    # ========================================================

    selected_normalized = {
        normalize_identifier(table)
        for table in selected_tables
    }

    # ========================================================
    # LOAD REAL DATABASE SCHEMA
    #
    # RAG determines semantic relevance.
    # MySQL foreign keys determine structural relationships.
    # ========================================================

    relationship_tables = []

    try:

        engine = get_database_engine(
            database
        )

        inspector = inspect(
            engine
        )

        all_tables = inspector.get_table_names()

    except Exception as exc:

        print(
            "\nRELATIONSHIP INSPECTION ERROR"
        )

        print(exc)

        all_tables = []
        inspector = None

    print("\nDATABASE TABLES FROM MYSQL")
    print("--------------------------")

    print(all_tables)

    # ========================================================
    # BUILD FOREIGN KEY GRAPH
    # ========================================================

    foreign_key_graph = {}

    if inspector is not None:

        for table in all_tables:

            try:

                foreign_keys = inspector.get_foreign_keys(
                    table
                )

            except Exception as exc:

                print(
                    f"FK inspection failed for {table}: {exc}"
                )

                foreign_keys = []

            references = set()

            for foreign_key in foreign_keys:

                referred_table = (
                    foreign_key.get(
                        "referred_table"
                    )
                )

                if referred_table:

                    references.add(
                        normalize_identifier(
                            referred_table
                        )
                    )

            foreign_key_graph[
                normalize_identifier(table)
            ] = references

    # ========================================================
    # PRINT FOREIGN KEY GRAPH
    # ========================================================

    print("\nFOREIGN KEY GRAPH")
    print("-----------------")

    for (
        table,
        references
    ) in foreign_key_graph.items():

        print(
            f"{table} -> {sorted(references)}"
        )

    # ========================================================
    # HIGH-CONFIDENCE TABLES
    #
    # We don't want a low-score table such as Department
    # to cause unrelated bridge tables such as Minor_in
    # to be selected.
    #
    # Example:
    #
    # Student = 15
    # Course = 2
    # Department = 1
    #
    # High-confidence:
    # Student + Course
    #
    # Therefore:
    # Student + Course -> Enrolled_in
    #
    # NOT:
    # Student + Department -> Minor_in
    # ========================================================

    high_confidence_selected = {
        normalize_identifier(table)
        for (
            score,
            table,
            _,
            _
        ) in scored_tables
        if (
            score >= 2
            and
            normalize_identifier(table)
            in selected_normalized
        )
    }

    print("\nHIGH CONFIDENCE TABLES")
    print("----------------------")

    print(
        sorted(
            high_confidence_selected
        )
    )

    # ========================================================
    # FIND RELATIONSHIP / BRIDGE TABLES
    #
    # A relationship table is useful when it references
    # at least two high-confidence selected tables.
    # ========================================================

    for table in all_tables:

        table_normalized = normalize_identifier(
            table
        )

        # Already selected
        if table_normalized in selected_normalized:
            continue

        references = foreign_key_graph.get(
            table_normalized,
            set()
        )

        selected_connections = (
            references
            &
            high_confidence_selected
        )

        if len(selected_connections) >= 2:

            relationship_tables.append(
                table
            )

    # ========================================================
    # EXPLICIT STUDENT + COURSE RELATIONSHIP
    #
    # college_3:
    #
    # Student
    #   StuID
    #      ↓
    # Enrolled_in
    #   StuID + CID
    #      ↓
    # Course
    #
    # This guarantees that Enrolled_in is included for
    # student-course enrollment questions.
    # ========================================================

    if (
        "student" in selected_normalized
        and
        "course" in selected_normalized
    ):

        for table in all_tables:

            table_normalized = normalize_identifier(
                table
            )

            if table_normalized in selected_normalized:
                continue

            references = foreign_key_graph.get(
                table_normalized,
                set()
            )

            if (
                "student" in references
                and
                "course" in references
            ):

                if table not in relationship_tables:

                    relationship_tables.append(
                        table
                    )

    # ========================================================
    # NORMALIZE / DEDUPLICATE RELATIONSHIP TABLES
    # ========================================================

    unique_relationship_tables = []

    seen_relationships = set()

    for table in relationship_tables:

        normalized = normalize_identifier(
            table
        )

        if normalized in seen_relationships:
            continue

        seen_relationships.add(
            normalized
        )

        unique_relationship_tables.append(
            table
        )

    relationship_tables = (
        unique_relationship_tables
    )

    print("\nRELATIONSHIP TABLES")
    print("-------------------")

    print(
        relationship_tables
    )

    # ========================================================
    # ADD RELATIONSHIP TABLES TO SELECTED SET
    # ========================================================

    for table in relationship_tables:

        table_normalized = normalize_identifier(
            table
        )

        if table_normalized not in selected_normalized:

            selected_tables.append(
                table
            )

            selected_normalized.add(
                table_normalized
            )

    # ========================================================
    # GET SCHEMA DOCUMENTS
    #
    # IMPORTANT:
    # Compare table names using normalize_identifier().
    #
    # This fixes:
    #
    # Enrolled_in
    # enrolled_in
    #
    # being treated as different tables.
    # ========================================================

    selected_docs = []

    added_schema_tables = set()

    for (
        score,
        table,
        document,
        metadata
    ) in scored_tables:

        table_normalized = normalize_identifier(
            table
        )

        if table_normalized in selected_normalized:

            selected_docs.append(
                document
            )

            added_schema_tables.add(
                table_normalized
            )

    # ========================================================
    # ADD SCHEMA FOR RELATIONSHIP TABLES
    #
    # If the relationship table exists in Chroma, it was
    # already added above.
    #
    # If it doesn't exist in Chroma, build its schema
    # directly from MySQL.
    # ========================================================

    if inspector is not None:

        for table in relationship_tables:

            table_normalized = normalize_identifier(
                table
            )

            if table_normalized in added_schema_tables:
                continue

            try:

                columns = inspector.get_columns(
                    table
                )

                foreign_keys = inspector.get_foreign_keys(
                    table
                )

                relationship_schema = (
                    f"\n\nTABLE: {table}\n"
                )

                relationship_schema += (
                    "COLUMNS:\n"
                )

                for column in columns:

                    relationship_schema += (
                        f"- {column['name']} "
                        f"({column['type']})\n"
                    )

                if foreign_keys:

                    relationship_schema += (
                        "FOREIGN KEYS:\n"
                    )

                    for foreign_key in foreign_keys:

                        constrained_columns = (
                            foreign_key.get(
                                "constrained_columns",
                                []
                            )
                        )

                        referred_table = (
                            foreign_key.get(
                                "referred_table"
                            )
                        )

                        referred_columns = (
                            foreign_key.get(
                                "referred_columns",
                                []
                            )
                        )

                        relationship_schema += (
                            f"- {constrained_columns} "
                            f"-> "
                            f"{referred_table}"
                            f"{referred_columns}\n"
                        )

                selected_docs.append(
                    relationship_schema
                )

                added_schema_tables.add(
                    table_normalized
                )

            except Exception as exc:

                print(
                    f"Could not extract relationship "
                    f"schema for {table}: {exc}"
                )

    # ========================================================
    # BUILD FINAL SCHEMA
    # ========================================================

    schema = "\n\n".join(
        selected_docs
    )

    # ========================================================
    # FINAL LOGGING
    # ========================================================

    print("\nSELECTED TABLES")
    print("----------------")

    for table in selected_tables:

        print(
            table
        )

    print(
        f"\nSCHEMA LENGTH: {len(schema)}"
    )

    return {
        "schema": schema
    }


@traced_node("clarification_detector_node")
def clarification_detector_node(state: QueryState):

    question = state["question"]

    prompt = f"""
You are the ambiguity detector for a Text-to-SQL system.

Your job is to determine whether the user's question contains
ENOUGH information to generate a precise SQL query.

A question is AMBIGUOUS if the user has not specified what
information they actually want.

Examples of ambiguous questions:
- "Show me the students."
- "Give me the faculty."
- "Show the activities."
- "What about the students?"

Examples of clear questions:
- "Show me the names of all students."
- "Show me the ages of all students."
- "Which students participate in Mountain Climbing?"
- "How many students are there?"

IMPORTANT:
If a question could reasonably mean "show me everything",
you may consider it clear.

If the requested information is genuinely unspecified,
mark it as ambiguous.

Return exactly:

CLEAR: YES
QUESTION:

or

CLEAR: NO
QUESTION: <one concise clarification question>

User question:
{question}
"""

    response = llm.invoke(prompt)

    output = extract_text(response).strip()

    clear = False
    clarification_question = ""

    match = re.search(
        r"CLEAR\s*:\s*(YES|NO)",
        output,
        flags=re.IGNORECASE
    )

    if match:
        clear = (
            match.group(1).upper() == "YES"
        )

    question_match = re.search(
        r"QUESTION\s*:\s*(.*)",
        output,
        flags=re.IGNORECASE | re.DOTALL
    )

    if question_match:
        clarification_question = (
            question_match.group(1).strip()
        )

    if not clear:
        print("\nAMBIGUOUS QUESTION")
        print("------------------")
        print(
            clarification_question
        )

    return {
        "clear": clear,
        "clarification_question":
            clarification_question
    }


@traced_node("clarification_node")
def clarification_node(state: QueryState):

    clarification_question = state.get(
        "clarification_question",
        ""
    )

    print("\nCLARIFICATION")
    print("----------------")
    print(
        clarification_question
    )

    answer = input(
        "\nYour clarification: "
    )

    return {
        "user_clarification": answer
    }


@traced_node("rewrite_question_node")
def rewrite_question_node(state: QueryState):

    original_question = state["question"]

    clarification = state.get(
        "user_clarification",
        ""
    )

    prompt = f"""
Rewrite the user's original question into one
complete, self-contained Text-to-SQL question.

Original question:
{original_question}

User clarification:
{clarification}

Return only the rewritten question.
"""

    response = llm.invoke(prompt)

    rewritten = extract_text(response).strip()

    print("\nREWRITTEN QUESTION")
    print("------------------")
    print(rewritten)

    return {
        "question": rewritten,
        "clear": True
    }


@traced_node("sql_generation_node")
def sql_generation_node(state: QueryState):

    question = state["question"]
    database = state["database"]
    schema = state["schema"]

    prompt = f"""
You are an expert MySQL Text-to-SQL generator.

Generate ONE read-only SQL query.

DATABASE:
{database}

USER QUESTION:
{question}

AVAILABLE SCHEMA:
{schema}

Rules:
1. Use ONLY tables and columns present in the schema.
2. Use the exact table and column names.
3. Do not invent tables or columns.
4. Generate MySQL-compatible SQL.
5. The query must be read-only.
6. Do not use INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE.
7. Prefer normal table names without database qualification.
8. Return ONLY SQL.
"""

    response = llm.invoke(prompt)

    sql = clean_sql(
        extract_text(response)
    )

    print("\nGENERATED SQL")
    print("----------------")
    print(sql)

    return {
        "sql": sql
    }


def extract_table_names_from_sql(sql):
    tables = []

    patterns = [
        r"\bFROM\s+([A-Za-z0-9_`.]+)",
        r"\bJOIN\s+([A-Za-z0-9_`.]+)"
    ]

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

            parts = table.split(".")

            table = parts[-1]

        table = table.strip("`")

        cleaned.append(
            normalize_identifier(table)
        )

    return cleaned


def extract_column_references(sql):
    references = []

    pattern = r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b"

    matches = re.findall(
        pattern,
        sql
    )

    for table_alias, column in matches:

        references.append(
            (
                normalize_identifier(table_alias),
                normalize_identifier(column)
            )
        )

    return references


def validate_sql(state: QueryState):

    sql = state["sql"]
    database = state["database"]
    schema = state["schema"]

    try:

        engine = get_database_engine(
            database
        )

        inspector = inspect(engine)

        actual_tables = inspector.get_table_names()

        table_map = {
            normalize_identifier(table): table
            for table in actual_tables
        }

        schema_tables = {}

        for block in schema.split("\n\n"):

            match = re.search(
                r"TABLE:\s*([A-Za-z0-9_]+)",
                block,
                flags=re.IGNORECASE
            )

            if not match:
                continue

            table = normalize_identifier(
                match.group(1)
            )

            columns = re.findall(
                r"^\s*-\s*([A-Za-z0-9_]+)\s*\(",
                block,
                flags=re.MULTILINE
            )

            schema_tables[table] = {
                normalize_identifier(c)
                for c in columns
            }

        print("\nVALIDATOR SCHEMA")
        print("----------------")

        for table, columns in schema_tables.items():

            print(
                f"{table} -> {sorted(columns)}"
            )

        sql_upper = sql.upper()

        forbidden = [
            "INSERT ",
            "UPDATE ",
            "DELETE ",
            "DROP ",
            "ALTER ",
            "CREATE ",
            "TRUNCATE ",
            "REPLACE "
        ]

        for keyword in forbidden:

            if keyword in sql_upper:

                return {
                    "validation_valid": False,
                    "validation_message":
                        f"Forbidden SQL operation: {keyword.strip()}"
                }

        sql_tables = extract_table_names_from_sql(
            sql
        )

        missing_tables = []

        for table in sql_tables:

            if table not in table_map:
                missing_tables.append(
                    table
                )

        if missing_tables:

            return {
                "validation_valid": False,
                "validation_message":
                    "Tables not present in database: "
                    + ", ".join(missing_tables)
            }

        qualified_table_pattern = (
            r"(?:FROM|JOIN)\s+"
            r"([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)"
        )

        qualified_matches = re.findall(
            qualified_table_pattern,
            sql,
            flags=re.IGNORECASE
        )

        for db_name, table_name in qualified_matches:

            if normalize_identifier(
                db_name
            ) != normalize_identifier(
                database
            ):

                return {
                    "validation_valid": False,
                    "validation_message":
                        f"SQL references database "
                        f"'{db_name}' instead of "
                        f"'{database}'."
                }

        alias_map = {}

        alias_pattern = (
            r"\b(?:FROM|JOIN)\s+"
            r"(?:[A-Za-z0-9_]+\.)?"
            r"`?([A-Za-z0-9_]+)`?"
            r"(?:\s+AS)?\s+"
            r"`?([A-Za-z_][A-Za-z0-9_]*)`?"
        )

        alias_matches = re.findall(
            alias_pattern,
            sql,
            flags=re.IGNORECASE
        )

        for table, alias in alias_matches:

            table_norm = normalize_identifier(
                table
            )

            alias_norm = normalize_identifier(
                alias
            )

            if (
                table_norm in table_map
                and alias_norm not in {
                    "on",
                    "where",
                    "join",
                    "left",
                    "right",
                    "inner",
                    "outer",
                    "group",
                    "order",
                    "limit"
                }
            ):

                alias_map[alias_norm] = table_norm

        column_references = extract_column_references(
            sql
        )

        missing_columns = []

        for prefix, column in column_references:

            if prefix in schema_tables:

                table_name = prefix

            elif prefix in alias_map:

                table_name = alias_map[prefix]

            else:

                continue

            if table_name not in schema_tables:
                continue

            if column not in schema_tables[table_name]:

                missing_columns.append(
                    f"{table_name}.{column}"
                )

        if missing_columns:

            return {
                "validation_valid": False,
                "validation_message":
                    "Columns not present in retrieved schema: "
                    + ", ".join(
                        sorted(
                            set(missing_columns)
                        )
                    )
            }

        print("\nSQL VALIDATION")
        print("----------------")
        print("Valid: True")
        print("Message: SQL is valid.")

        return {
            "validation_valid": True,
            "validation_message":
                "SQL is valid."
        }

    except Exception as e:

        return {
            "validation_valid": False,
            "validation_message":
                f"Validation error: {str(e)}"
        }


@traced_node("sql_validation_node")
def sql_validation_node(state: QueryState):

    sql = state.get("sql", "")
    database = state.get("database", "")

    guardrail = validate_sql_guardrail(sql, database)
    if not guardrail.allowed:
        result = {
            "validation_valid": False,
            "validation_message": f"SQL guardrail blocked query: {guardrail.reason}"
        }
    else:
        result = validate_sql(state)

    print("\nSQL VALIDATION")
    print("----------------")
    print(
        f"Valid: {result['validation_valid']}"
    )
    print(
        f"Message: {result['validation_message']}"
    )

    return result


@traced_node("sql_correction_node")
def sql_correction_node(state: QueryState):

    retry_count = state.get(
        "retry_count",
        0
    )

    retry_count += 1

    print("\nSQL CORRECTION")
    print("----------------")
    print(
        f"Retry: {retry_count}"
    )

    question = state["question"]
    database = state["database"]
    schema = state["schema"]
    sql = state["sql"]

    validation_message = state.get(
        "validation_message",
        ""
    )

    execution_error = state.get(
        "execution_error",
        ""
    )

    prompt = f"""
You are fixing a MySQL Text-to-SQL query.

DATABASE:
{database}

USER QUESTION:
{question}

AVAILABLE SCHEMA:
{schema}

CURRENT SQL:
{sql}

VALIDATION ERROR:
{validation_message}

EXECUTION ERROR:
{execution_error}

Rules:
1. Use ONLY tables and columns in the provided schema.
2. The active database is exactly: {database}
3. Do not reference another database.
4. Prefer unqualified table names.
5. Fix the actual error.
6. Keep the query read-only.
7. Return ONLY corrected SQL.
"""

    response = llm.invoke(prompt)

    corrected_sql = clean_sql(
        extract_text(response)
    )

    print("\nCORRECTED SQL")
    print("----------------")
    print(corrected_sql)

    return {
        "sql": corrected_sql,
        "retry_count": retry_count,
        "execution_error": ""
    }


@traced_node("execute_sql_node")
def execute_sql_node(state: QueryState):

    database = state["database"]
    sql = state["sql"]

    print("\nFINAL SQL")
    print("----------------")
    print(sql)

    try:

        engine = get_database_engine(
            database
        )

        with engine.connect() as connection:

            result = connection.execute(
                text(sql)
            )

            rows = result.mappings().all()

        rows = [
            dict(row)
            for row in rows
        ]
        rows = sanitize_result_rows(rows)

        print("\nRESULT")
        print("----------------")
        print(rows)

        return {
            "result": rows,
            "execution_error": ""
        }

    except SQLAlchemyError as e:

        error = str(e)

        print("\nSQL EXECUTION ERROR")
        print("----------------")
        print(error)

        return {
            "result": None,
            "execution_error": error
        }


def should_clarify(state: QueryState):

    if state.get("clear", False):
        return "database_router"

    return "clarification"


def after_validation(state: QueryState):

    if state.get(
        "validation_valid",
        False
    ):
        return "execution"

    retry_count = state.get(
        "retry_count",
        0
    )

    if retry_count < 2:
        return "correction"

    return END


def after_execution(state: QueryState):

    error = state.get(
        "execution_error",
        ""
    )

    if not error:
        return END

    retry_count = state.get(
        "retry_count",
        0
    )

    if retry_count < 2:
        return "correction"

    return END


@traced_node("build_graph")
def build_graph():

    graph = StateGraph(
        QueryState
    )

    graph.add_node(
        "clarify",
        clarification_detector_node
    )

    graph.add_node(
        "clarification",
        clarification_node
    )

    graph.add_node(
        "rewrite_question",
        rewrite_question_node
    )

    graph.add_node(
        "database_router",
        database_router_node
    )

    graph.add_node(
        "database_clarification",
        database_clarification_node
    )

    graph.add_node(
        "database_selection",
        database_selection_node
    )

    graph.add_node(
        "rag",
        rag_node
    )

    graph.add_node(
        "sql_generation",
        sql_generation_node
    )

    graph.add_node(
        "sql_validation",
        sql_validation_node
    )

    graph.add_node(
        "correction",
        sql_correction_node
    )

    graph.add_node(
        "execution",
        execute_sql_node
    )

    graph.add_edge(
        START,
        "clarify"
    )

    graph.add_conditional_edges(
        "clarify",
        should_clarify,
        {
            "database_router":
                "database_router",

            "clarification":
                "clarification"
        }
    )

    graph.add_edge(
        "clarification",
        "rewrite_question"
    )

    graph.add_edge(
        "rewrite_question",
        "database_router"
    )

    graph.add_conditional_edges(
        "database_router",
        after_database_router,
        {
            "rag": "rag",
            "database_clarification": "database_clarification"
        }
    )

    graph.add_edge(
        "database_clarification",
        "database_selection"
    )

    graph.add_edge(
        "database_selection",
        "rag"
    )

    graph.add_edge(
        "rag",
        "sql_generation"
    )

    graph.add_edge(
        "sql_generation",
        "sql_validation"
    )

    graph.add_conditional_edges(
        "sql_validation",
        after_validation,
        {
            "execution":
                "execution",

            "correction":
                "correction",

            END:
                END
        }
    )

    graph.add_edge(
        "correction",
        "sql_validation"
    )

    graph.add_conditional_edges(
        "execution",
        after_execution,
        {
            "correction":
                "correction",

            END:
                END
        }
    )

    return graph.compile()


@traced_node("run_query")
def run_query(question: str):

    guardrail = validate_user_question(question)
    print("\nINPUT GUARDRAIL")
    print("----------------")
    print(f"Allowed: {guardrail.allowed}")
    print(f"Category: {guardrail.category}")
    print(f"Message: {guardrail.reason}")

    if not guardrail.allowed:
        raise ValueError(guardrail.reason)

    app = build_graph()

    initial_state = {
        "question": question,
        "clear": False,
        "clarification_question": "",
        "user_clarification": "",
        "database": "",
        "database_clarification_question": "",
        "user_database_clarification": "",
        "database_candidates": [],
        "schema": "",
        "sql": "",
        "execution_error": "",
        "result": None,
        "validation_valid": False,
        "validation_message": "",
        "retry_count": 0
    }

    final_state = app.invoke(initial_state)
    return final_state


def main():

    question = input("Question: ").strip()

    if not question:
        print("Question cannot be empty.")
        return

    status = tracing_status()
    print("\nLANGSMITH")
    print("----------------")
    print(f"Tracing enabled: {status['enabled']}")
    print(f"Project: {status['project']}")
    print(f"API key configured: {status['api_key_configured']}")

    try:
        final_state = run_query(question)
    except Exception as error:
        print("\nQUERY BLOCKED / FAILED")
        print("----------------")
        print(str(error))
        return

    final_text = (
        f"Question: {question}\n"
        f"Database: {final_state.get('database')}\n"
        f"SQL: {final_state.get('sql')}\n"
        f"Result: {final_state.get('result')}"
    )
    output_guardrail = validate_output(final_text)

    print("\nOUTPUT GUARDRAIL")
    print("----------------")
    print(f"Allowed: {output_guardrail.allowed}")
    print(f"Category: {output_guardrail.category}")
    print(f"Message: {output_guardrail.reason}")

    print("\n==============================")
    print("FINAL OUTPUT")
    print("==============================")
    print(f"Question: {question}")
    print(f"Database: {final_state.get('database')}")
    print(f"SQL: {final_state.get('sql')}")
    print(f"Result: {final_state.get('result')}")
    print(f"Retries: {final_state.get('retry_count', 0)}")


if __name__ == "__main__":
    main()
