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

from app import db_registry, queryclarify


# ---------------------------------------------------------
# Main API query function
# ---------------------------------------------------------
def process_query(
    question: str,
    clarification: str | None = None,
    database: str | None = None,
    client_id: str | None = None
) -> dict[str, Any]:

    question = question.strip()

    if not question:
        return {
            "status": "error",
            "message": "Question cannot be empty."
        }

    # -----------------------------------------------------
    # Authorise an explicitly selected database.
    #
    # Built-in databases are open to everyone. A user
    # database (u_xxxxxxxxxxxx) may only be queried by the
    # client that uploaded it.
    # -----------------------------------------------------

    owner_hash = db_registry.hash_owner(client_id)

    explicit_database = ""
    database_display_name = None

    if database and database.strip():

        try:
            record = db_registry.check_access(
                database,
                owner_hash
            )

        except db_registry.DatabaseAccessError:
            return {
                "status": "error",
                "question": question,
                "message":
                    "Database not found or not accessible."
            }

        explicit_database = (
            database.strip().strip("`").lower()
        )

        database = explicit_database

        if record is not None:
            database_display_name = record.display_name

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
    # 2. Database selection
    #
    # An explicitly chosen database skips routing entirely
    # (faster, and user databases never compete with the
    # built-in ones). Otherwise the router picks among the
    # built-in databases exactly as before.
    # -----------------------------------------------------

    candidates: list[str] = []

    if explicit_database:

        # -------------------------------------------------
        # IMPORTANT:
        #
        # The database has already been authorised above
        # using db_registry.check_access().
        #
        # Do NOT call database_selection_node() here.
        # That node belongs to the legacy built-in database
        # selection flow and rejects dynamic user database
        # IDs such as u_xxxxxxxxxxxx.
        # -------------------------------------------------

        state["user_database_clarification"] = explicit_database

        state["database"] = explicit_database

    else:

        routing_result = queryclarify.database_router_node(
            state
        )

        state.update(routing_result)

        candidates = state.get(
            "database_candidates",
            []
        )

        # -------------------------------------------------
        # Router was not confident: ask the user
        # -------------------------------------------------

        if not state.get("database", ""):

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

        "database_display_name":
            database_display_name,

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