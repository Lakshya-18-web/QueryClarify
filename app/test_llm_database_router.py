import os

from dotenv import load_dotenv

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_google_genai import ChatGoogleGenerativeAI


# --------------------------------------------------
# 1. Load environment variables
# --------------------------------------------------

load_dotenv()


# --------------------------------------------------
# 2. Initialize embeddings
# --------------------------------------------------

embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)


# --------------------------------------------------
# 3. Connect to database-level Chroma collection
# --------------------------------------------------

vectorstore = Chroma(
    collection_name="spiderman_databases",
    persist_directory="data/chroma",
    embedding_function=embeddings
)


# --------------------------------------------------
# 4. Initialize Gemini
# --------------------------------------------------

llm = ChatGoogleGenerativeAI(
    model="gemini-3.6-flash"
)


# --------------------------------------------------
# 5. Helper function
# --------------------------------------------------

def extract_text(response):

    content = response.content

    if isinstance(content, str):
        return content.strip()

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


# --------------------------------------------------
# 6. Database Router
# --------------------------------------------------

def route_database(question):

    # ----------------------------------------------
    # Retrieve top semantic candidates
    # ----------------------------------------------

    candidates = vectorstore.similarity_search(
    question,
    k=20
)

    candidate_text = ""

    for i, doc in enumerate(candidates, 1):

        database = doc.metadata.get("database")

        candidate_text += f"""
CANDIDATE {i}
DATABASE: {database}

SCHEMA:
{doc.page_content}

-------------------------
"""


    # ----------------------------------------------
    # Ask Gemini to select the best database
    # ----------------------------------------------

    prompt = f"""
You are a database router for a Text-to-SQL system.

Your task is to select the ONE database whose schema
can best answer the user's question.

IMPORTANT:

Do NOT choose a database merely because its name
contains words similar to the question.

Instead, carefully inspect:

- table names
- column names
- primary keys
- foreign keys
- relationships between tables
- entities requested by the question
- actions or relationships requested by the question

The selected database must contain the actual
entities and relationships needed to answer the question.

For example:

Question:
"Which students are participating in Mountain Climbing?"

A database containing:

Student
Activity
Participates_in

is more appropriate than a database containing:

mountain
climber

because the question specifically asks about STUDENTS
participating in an ACTIVITY.

Return ONLY the database name.

Do not explain your answer.

User question:
{question}

Candidate databases:
{candidate_text}

Selected database:
"""

    response = llm.invoke(prompt)

    database = extract_text(response)

    # ----------------------------------------------
    # Clean Gemini output
    # ----------------------------------------------

    database = database.replace("`", "").strip()

    return database


# --------------------------------------------------
# 7. Test questions
# --------------------------------------------------

questions = [

    "Which students are participating in Mountain Climbing?",

    "Show me the courses taken by students.",

    "Which players belong to each school?"

]


# --------------------------------------------------
# 8. Test database routing
# --------------------------------------------------

for question in questions:

    print("\n================================")
    print("QUESTION")
    print("================================")

    print(question)

    database = route_database(question)

    print("\nSELECTED DATABASE")
    print("================================")

    print(database)