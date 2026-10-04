import pandas as pd

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_google_genai import ChatGoogleGenerativeAI
from dotenv import load_dotenv


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
# 3. Pick 5 different databases
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
# 5. Database router collection
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
# 7. Helper
# --------------------------------------------------

def extract_text(response):

    content = response.content

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):

        parts = []

        for block in content:

            if isinstance(block, dict):

                if "text" in block:
                    parts.append(block["text"])

            elif isinstance(block, str):

                parts.append(block)

        return "".join(parts).strip()

    return str(content).strip()


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
DATABASE: {doc.metadata["database"]}

SCHEMA:
{doc.page_content}

-------------------------
"""


    # ----------------------------------------------
    # Ask Gemini to select database
    # ----------------------------------------------

    prompt = f"""
You are a database router for a Text-to-SQL system.

Select the ONE database that can correctly answer
the user's question.

Do NOT select a database simply because its name
contains similar words.

Inspect:

- entities
- tables
- columns
- primary keys
- foreign keys
- relationships

The selected database must contain the tables and
relationships required to answer the question.

USER QUESTION:
{question}

CANDIDATE DATABASES:
{candidate_text}

Return ONLY the database name.
Do not explain your answer.
"""

    response = llm.invoke(prompt)

    predicted = extract_text(response)

    predicted = predicted.replace("`", "").strip()


    # ----------------------------------------------
    # Print candidates for analysis
    # ----------------------------------------------

    candidate_databases = [
        doc.metadata["database"]
        for doc in candidates
    ]

    print("\nTOP 20 CANDIDATES:")
    print(candidate_databases)


    return predicted


# --------------------------------------------------
# 9. Run benchmark
# --------------------------------------------------

correct = 0
total = len(test_rows)


for _, row in test_rows.iterrows():

    question = row["question"]
    actual_database = row["database"]

    predicted_database = route_database(
        question
    )

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


    print("\nACTUAL DATABASE:")
    print("================================")

    print(actual_database)


    print("\nPREDICTED DATABASE:")
    print("================================")

    print(predicted_database)


    print("\nRESULT:")
    print("================================")

    print(
        "CORRECT"
        if is_correct
        else "WRONG"
    )


# --------------------------------------------------
# 10. Accuracy
# --------------------------------------------------

accuracy = (
    correct / total
) * 100


print("\n\n================================")
print("DATABASE ROUTING BENCHMARK")
print("================================")

print(f"Correct : {correct}/{total}")
print(f"Accuracy: {accuracy:.2f}%")