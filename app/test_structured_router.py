import pandas as pd

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_google_genai import ChatGoogleGenerativeAI


# --------------------------------------------------
# 1. Load environment
# --------------------------------------------------

load_dotenv()


# --------------------------------------------------
# 2. Load SpiderMan test dataset
# --------------------------------------------------

df = pd.read_csv(
    "data/spiderman/test_queries.csv"
)


# --------------------------------------------------
# 3. Use the same 5 benchmark questions
# --------------------------------------------------

test_rows = (
    df.drop_duplicates(subset=["database"])
      .head(5)
)


# --------------------------------------------------
# 4. Embeddings
# --------------------------------------------------

embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)


# --------------------------------------------------
# 5. Database-level Chroma
# --------------------------------------------------

vectorstore = Chroma(
    collection_name="spiderman_databases",
    persist_directory="data/chroma",
    embedding_function=embeddings
)


# --------------------------------------------------
# 6. Gemini
# --------------------------------------------------

llm = ChatGoogleGenerativeAI(
    model="gemini-3.6-flash"
)


# --------------------------------------------------
# 7. Structured router output
# --------------------------------------------------

class RouterResult(BaseModel):

    database: str = Field(
        description="The selected database name"
    )

    confidence: str = Field(
        description="HIGH if one database is clearly better, otherwise LOW"
    )

    ambiguous: bool = Field(
        description="True if multiple candidate databases are genuinely plausible"
    )


structured_llm = llm.with_structured_output(
    RouterResult
)


# --------------------------------------------------
# 8. Database Router
# --------------------------------------------------

def route_database(question):

    # ----------------------------------------------
    # Retrieve top 20 candidate databases
    # ----------------------------------------------

    candidates = vectorstore.similarity_search(
        question,
        k=20
    )

    candidate_text = ""

    for i, doc in enumerate(candidates, 1):

        candidate_text += f"""
CANDIDATE {i}

DATABASE:
{doc.metadata["database"]}

SCHEMA:
{doc.page_content}

-------------------------
"""


    # ----------------------------------------------
    # Schema-aware routing prompt
    # ----------------------------------------------

    prompt = f"""
You are the database routing component of QueryClarify,
a schema-aware Text-to-SQL system.

Your task is to identify which database can answer
the user's question.

IMPORTANT:

Do NOT choose a database only because its name is
semantically similar to words in the question.

Inspect the actual schema.

Consider:

1. Entities requested by the user.
2. Relevant table names.
3. Relevant column names.
4. Primary keys.
5. Foreign keys.
6. Relationships between tables.
7. Whether the requested operation can actually
   be performed using that database.

IMPORTANT:
Multiple databases may contain similarly named tables.

For example:

Question:
"How many teachers are there?"

If one database contains:
teacher(Teacher_ID, Name, Age, Hometown)

and another contains:
teachers(LastName, FirstName, Classroom)

then BOTH may plausibly answer the question.

In that situation:

- choose the database that is most appropriate
- set ambiguous = true if multiple databases remain
  genuinely plausible
- set confidence = LOW

If only one database clearly matches:

- ambiguous = false
- confidence = HIGH

User question:
{question}

Candidate databases:
{candidate_text}

Return:
- database
- confidence
- ambiguous
"""

    result = structured_llm.invoke(prompt)

    return result, candidates


# --------------------------------------------------
# 9. Run benchmark
# --------------------------------------------------

correct = 0
total = len(test_rows)


for _, row in test_rows.iterrows():

    question = row["question"]
    actual_database = row["database"]

    result, candidates = route_database(question)

    predicted_database = result.database

    is_correct = (
        predicted_database.lower()
        == actual_database.lower()
    )

    if is_correct:
        correct += 1


    print("\n================================")
    print("QUESTION")
    print("================================")

    print(question)


    print("\nACTUAL DATABASE")
    print("================================")

    print(actual_database)


    print("\nTOP 20 CANDIDATES")
    print("================================")

    print([
        doc.metadata["database"]
        for doc in candidates
    ])


    print("\nROUTER DECISION")
    print("================================")

    print("Database :", result.database)
    print("Confidence:", result.confidence)
    print("Ambiguous :", result.ambiguous)


    print("\nRESULT")
    print("================================")

    print(
        "CORRECT"
        if is_correct
        else "WRONG"
    )


# --------------------------------------------------
# 10. Benchmark accuracy
# --------------------------------------------------

accuracy = (
    correct / total
) * 100


print("\n\n================================")
print("STRUCTURED DATABASE ROUTER")
print("================================")

print(f"Correct : {correct}/{total}")
print(f"Accuracy: {accuracy:.2f}%")