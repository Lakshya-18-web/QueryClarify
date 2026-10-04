import pathlib
import sys

# Make `app` importable when run as `python app/<script>.py` from the repo root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from typing import TypedDict
import os

from dotenv import load_dotenv
from pydantic import BaseModel

from langgraph.graph import StateGraph, START, END

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

from sqlalchemy import create_engine, text

from app.admin_credentials import mysql_url, read_only_settings


# ==================================================
# 1. LOAD ENVIRONMENT VARIABLES
# ==================================================

load_dotenv()

# Credentials come from the environment (app/.env); nothing is hard-coded.
mysql_settings = read_only_settings()


# ==================================================
# 2. MYSQL CONNECTION
# ==================================================

engine = create_engine(mysql_url(mysql_settings, "activity_1"))


# ==================================================
# 3. QUERY STATE
# ==================================================

class QueryState(TypedDict):
    question: str
    clear: bool
    clarification_question: str
    user_clarification: str
    schema: str
    sql: str
    result: str


# ==================================================
# 4. LANGGRAPH
# ==================================================

graph = StateGraph(QueryState)


# ==================================================
# 5. CLARIFICATION STRUCTURED OUTPUT
# ==================================================

class ClarificationResult(BaseModel):
    clear: bool
    clarification_question: str


# ==================================================
# 6. GEMINI
# ==================================================

llm = ChatGoogleGenerativeAI(
    model="gemini-3.6-flash"
)

structured_llm = llm.with_structured_output(
    ClarificationResult
)


# ==================================================
# 7. EMBEDDINGS
# ==================================================

embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)


# ==================================================
# 8. CHROMA
# ==================================================

vectorstore = Chroma(
    collection_name="schema_test_v2",
    persist_directory="data/chroma",
    embedding_function=embeddings
)


# ==================================================
# 9. HELPER: EXTRACT TEXT FROM GEMINI RESPONSE
# ==================================================

def extract_text(response):

    content = response.content

    # Normal string response
    if isinstance(content, str):
        return content.strip()

    # Gemini structured content response
    if isinstance(content, list):

        text_parts = []

        for block in content:

            if isinstance(block, dict):

                if "text" in block:
                    text_parts.append(block["text"])

            elif isinstance(block, str):

                text_parts.append(block)

        return "".join(text_parts).strip()

    return str(content).strip()


# ==================================================
# 10. CLARIFICATION NODE
# ==================================================

def clarify_node(state: QueryState):

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

If ambiguous:
- clear = false
- ask ONE concise clarification question.

If clear:
- clear = true
- clarification_question = ""

User question:
{question}
"""

    response = structured_llm.invoke(prompt)

    return {
        "clear": response.clear,
        "clarification_question": response.clarification_question
    }

# ==================================================
# 11. INTERACTIVE CLARIFICATION NODE
# ==================================================

def clarification_node(state: QueryState):

    clarification_question = state["clarification_question"]

    print("\n================================")
    print("CLARIFICATION REQUIRED")
    print("================================")

    print(clarification_question)

    user_answer = input("\nYour clarification: ")

    return {
        "user_clarification": user_answer
    }


# ==================================================
# 12. REWRITE QUESTION NODE
# ==================================================

def rewrite_question_node(state: QueryState):

    question = state["question"]

    clarification_question = state["clarification_question"]

    user_clarification = state["user_clarification"]

    prompt = f"""
You are a query-understanding assistant for a Text-to-SQL system.

Rewrite the user's original question using the clarification
provided by the user.

The rewritten question must be:

- clear
- specific
- self-contained
- suitable for SQL generation

Do not add information that the user did not provide.

Original question:
{question}

Clarification question:
{clarification_question}

User's clarification:
{user_clarification}

Return ONLY the rewritten question.
"""

    response = llm.invoke(prompt)

    rewritten_question = extract_text(response)

    return {
        "question": rewritten_question
    }


# ==================================================
# 13. RAG NODE
# ==================================================

def rag_node(state: QueryState):

    question = state["question"]

    results = vectorstore.similarity_search(
        question,
        k=3
    )

    schema = "\n\n".join(
        result.page_content
        for result in results
    )

    return {
        "schema": schema
    }


# ==================================================
# 14. SQL GENERATION NODE
# ==================================================

def sql_generation_node(state: QueryState):

    question = state["question"]

    schema = state["schema"]

    prompt = f"""
You are an expert MySQL Text-to-SQL system.

Generate a SQL query that correctly answers the user's question.

Rules:

1. Use ONLY tables and columns present in the retrieved schema.
2. Use MySQL syntax.
3. Follow the relationships between tables.
4. Do not invent tables.
5. Do not invent columns.
6. Use JOINs when required by the schema relationships.
7. Generate only the SQL query.
8. Do not use markdown code fences.
9. Do not provide explanations.

Retrieved schema:
{schema}

User question:
{question}

SQL query:
"""

    response = llm.invoke(prompt)

    sql = extract_text(response)

    # ----------------------------------------------
    # Remove markdown fences
    # ----------------------------------------------

    if sql.startswith("```sql"):

        sql = sql[len("```sql"):]

    elif sql.startswith("```"):

        sql = sql[len("```"):]

    if sql.endswith("```"):

        sql = sql[:-3]

    sql = sql.strip()

    return {
        "sql": sql
    }


# ==================================================
# 15. SQL EXECUTION NODE
# ==================================================

def sql_execution_node(state: QueryState):

    sql = state["sql"]

    try:

        with engine.connect() as connection:

            query_result = connection.execute(
                text(sql)
            )

            rows = query_result.fetchall()

            columns = query_result.keys()

            output = [
                dict(zip(columns, row))
                for row in rows
            ]

        return {
            "result": str(output)
        }

    except Exception as e:

        return {
            "result": f"SQL execution error: {str(e)}"
        }


# ==================================================
# 16. ROUTING
# ==================================================

def route_after_clarify(state: QueryState):

    if state["clear"]:
        return "rag"

    return "clarification"


# ==================================================
# 17. ADD NODES
# ==================================================

graph.add_node(
    "clarify",
    clarify_node
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
    "rag",
    rag_node
)

graph.add_node(
    "sql_generation",
    sql_generation_node
)

graph.add_node(
    "sql_execution",
    sql_execution_node
)


# ==================================================
# 18. GRAPH CONNECTIONS
# ==================================================

graph.add_edge(
    START,
    "clarify"
)


graph.add_conditional_edges(
    "clarify",
    route_after_clarify,
    {
        "rag": "rag",
        "clarification": "clarification"
    }
)


graph.add_edge(
    "clarification",
    "rewrite_question"
)


graph.add_edge(
    "rewrite_question",
    "rag"
)


graph.add_edge(
    "rag",
    "sql_generation"
)


graph.add_edge(
    "sql_generation",
    "sql_execution"
)


graph.add_edge(
    "sql_execution",
    END
)


# ==================================================
# 19. COMPILE GRAPH
# ==================================================

app = graph.compile()


# ==================================================
# 20. TEST
# ==================================================

if __name__ == "__main__":

    result = app.invoke({

        "question":
            "Show me the students.",

        "clear":
            False,

        "clarification_question":
            "",

        "user_clarification":
            "",

        "schema":
            "",

        "sql":
            "",

        "result":
            ""
    })


    # ----------------------------------------------
    # FINAL OUTPUT
    # ----------------------------------------------

    print("\n================================")
    print("FINAL GRAPH RESULT")
    print("================================")

    print(result)


    print("\n================================")
    print("FINAL QUESTION")
    print("================================")

    print(result["question"])


    print("\n================================")
    print("RETRIEVED SCHEMA")
    print("================================")

    print(result["schema"])


    print("\n================================")
    print("GENERATED SQL")
    print("================================")

    print(result["sql"])


    print("\n================================")
    print("SQL RESULT")
    print("================================")

    print(result["result"])