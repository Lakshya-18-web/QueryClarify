import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------
# Make the project root importable
# ---------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------
# Import existing QueryClarify engine
# ---------------------------------------------------------

from app import queryclarify


# ---------------------------------------------------------
# Main API query function
# ---------------------------------------------------------

def process_query(
    question: str,
    clarification: str | None = None,
    database: str | None = None
) -> dict[str, Any]:

    question = question.strip()

    if not question:
        return {
            "status": "error",
            "message": "Question cannot be empty."
        }

    # -----------------------------------------------------
    # Initial state
    # -----------------------------------------------------

    state = {
        "question": question,

        "clear": False,

        "clarification_question": "",

        "user_clarification": clarification or "",

        "database": database or "",

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

    # -----------------------------------------------------
    # 1. Check question clarity
    # -----------------------------------------------------

    clarity_result = queryclarify.clarification_detector_node(
        state
    )

    state.update(clarity_result)

    # -----------------------------------------------------
    # If ambiguous and no clarification supplied
    # -----------------------------------------------------

    if not state.get("clear", False):

        if not clarification:

            return {
                "status": "clarification_required",

                "question": question,

                "clarification_required": True,

                "clarification_type": "question",

                "clarification_question":
                    state.get(
                        "clarification_question",
                        "Could you clarify your question?"
                    ),

                "database_candidates": []
            }

        # -------------------------------------------------
        # User supplied clarification
        # -------------------------------------------------

        state["user_clarification"] = clarification

        rewritten = queryclarify.rewrite_question_node(
            state
        )

        state.update(rewritten)

        state["clear"] = True

    # -----------------------------------------------------
    # 2. Database routing
    # -----------------------------------------------------

    routing_result = queryclarify.database_router_node(
        state
    )

    state.update(routing_result)

    # -----------------------------------------------------
    # If database wasn't confidently selected
    # -----------------------------------------------------

    selected_database = state.get(
        "database",
        ""
    )

    candidates = state.get(
        "database_candidates",
        []
    )

    if not selected_database:

        if not database:

            return {
                "status": "clarification_required",

                "question":
                    state.get(
                        "question",
                        question
                    ),

                "clarification_required": True,

                "clarification_type": "database",

                "clarification_question":
                    state.get(
                        "database_clarification_question",
                        "Which database should I query?"
                    ),

                "database_candidates": candidates
            }

    # -----------------------------------------------------
    # User explicitly selected a database
    # -----------------------------------------------------

    if database:

        state["user_database_clarification"] = database

        selection_result = (
            queryclarify.database_selection_node(
                state
            )
        )

        state.update(selection_result)

        if not state.get("database"):

            return {
                "status": "error",

                "question":
                    state.get(
                        "question",
                        question
                    ),

                "message":
                    "Invalid database selected."
            }

    # -----------------------------------------------------
    # 3. RAG
    # -----------------------------------------------------

    rag_result = queryclarify.rag_node(
        state
    )

    state.update(rag_result)

    # -----------------------------------------------------
    # 4. SQL generation
    # -----------------------------------------------------

    sql_result = queryclarify.sql_generation_node(
        state
    )

    state.update(sql_result)

    # -----------------------------------------------------
    # 5. SQL validation
    # -----------------------------------------------------

    validation_result = (
        queryclarify.sql_validation_node(
            state
        )
    )

    state.update(validation_result)

    # -----------------------------------------------------
    # 6. SQL correction loop
    # -----------------------------------------------------

    while (
        not state.get("validation_valid", False)
        and state.get("retry_count", 0) < 2
    ):

        correction_result = (
            queryclarify.sql_correction_node(
                state
            )
        )

        state.update(correction_result)

        validation_result = (
            queryclarify.sql_validation_node(
                state
            )
        )

        state.update(validation_result)

    # -----------------------------------------------------
    # Stop if SQL is still invalid
    # -----------------------------------------------------

    if not state.get("validation_valid", False):

        return {
            "status": "validation_failed",

            "question":
                state.get(
                    "question",
                    question
                ),

            "database":
                state.get("database"),

            "sql":
                state.get("sql"),

            "validation_valid": False,

            "validation_message":
                state.get(
                    "validation_message",
                    "SQL validation failed."
                ),

            "retries":
                state.get(
                    "retry_count",
                    0
                )
        }

    # -----------------------------------------------------
    # 7. Execute SQL
    # -----------------------------------------------------

    execution_result = (
        queryclarify.execute_sql_node(
            state
        )
    )

    state.update(execution_result)

    # -----------------------------------------------------
    # Execution error
    # -----------------------------------------------------

    if state.get("execution_error"):

        return {
            "status": "execution_failed",

            "question":
                state.get(
                    "question",
                    question
                ),

            "database":
                state.get("database"),

            "sql":
                state.get("sql"),

            "validation_valid":
                state.get(
                    "validation_valid",
                    False
                ),

            "validation_message":
                state.get(
                    "validation_message",
                    ""
                ),

            "execution_error":
                state.get(
                    "execution_error"
                ),

            "retries":
                state.get(
                    "retry_count",
                    0
                )
        }

    # -----------------------------------------------------
    # 8. Extract retrieved tables
    # -----------------------------------------------------

    retrieved_tables = extract_tables(
        state.get(
            "schema",
            ""
        )
    )

    # -----------------------------------------------------
    # Final successful response
    # -----------------------------------------------------

    return {
        "status": "success",

        "question":
            state.get(
                "question",
                question
            ),

        "database":
            state.get(
                "database"
            ),

        "clarification_required": False,

        "clarification_type": None,

        "clarification_question": None,

        "database_candidates":
            candidates,

        "sql":
            state.get(
                "sql"
            ),

        "result":
            state.get(
                "result"
            ),

        "retrieved_tables":
            retrieved_tables,

        "validation_valid":
            state.get(
                "validation_valid",
                False
            ),

        "validation_message":
            state.get(
                "validation_message",
                ""
            ),

        "execution_error": None,

        "retries":
            state.get(
                "retry_count",
                0
            ),

        "message":
            "Query executed successfully."
    }


# ---------------------------------------------------------
# Extract table names from retrieved schema
# ---------------------------------------------------------

def extract_tables(schema: str) -> list[str]:

    tables = []

    for line in schema.splitlines():

        line = line.strip()

        if line.startswith("TABLE:"):

            table_name = (
                line
                .replace("TABLE:", "")
                .strip()
            )

            if table_name and table_name not in tables:

                tables.append(
                    table_name
                )

    return tables