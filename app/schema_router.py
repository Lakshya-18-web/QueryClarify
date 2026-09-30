import pandas as pd

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings


# --------------------------------------------------
# 1. Load SpiderMan test dataset
# --------------------------------------------------

df = pd.read_csv(
    "data/spiderman/test_queries.csv"
)


# --------------------------------------------------
# 2. Embeddings
# --------------------------------------------------

embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)


# --------------------------------------------------
# 3. Database-level Chroma
# --------------------------------------------------

vectorstore = Chroma(
    collection_name="spiderman_databases",
    persist_directory="data/chroma",
    embedding_function=embeddings
)


# --------------------------------------------------
# 4. Extract important words from question
# --------------------------------------------------

def tokenize(text):

    text = text.lower()

    for char in "?,.!'\"():;":
        text = text.replace(char, " ")

    return set(text.split())


# --------------------------------------------------
# 5. Calculate schema relevance
# --------------------------------------------------

def schema_score(question, schema):

    question_words = tokenize(question)
    schema_words = tokenize(schema)

    # Exact word overlap
    overlap = question_words.intersection(schema_words)

    score = len(overlap)

    return score


# --------------------------------------------------
# 6. Retrieve candidate databases
# --------------------------------------------------

def retrieve_candidates(question, k=20):

    results = vectorstore.similarity_search(
        question,
        k=k
    )

    return results


# --------------------------------------------------
# 7. Rank databases using schema overlap
# --------------------------------------------------

def rank_databases(question, candidates):

    ranked = []

    for doc in candidates:

        database = doc.metadata["database"]

        schema = doc.page_content

        score = schema_score(
            question,
            schema
        )

        ranked.append(
            {
                "database": database,
                "score": score,
                "schema": schema
            }
        )

    ranked.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return ranked


# --------------------------------------------------
# 8. Test questions
# --------------------------------------------------

questions = [

    "Which students are participating in Mountain Climbing?",

    "How many teachers are there?",

    "How many documents do we have?",

    "How many singers do we have?",

    "Which players belong to each school?"

]


# --------------------------------------------------
# 9. Run router
# --------------------------------------------------

for question in questions:

    print("\n================================")
    print("QUESTION")
    print("================================")

    print(question)

    candidates = retrieve_candidates(
        question,
        k=20
    )

    ranked = rank_databases(
        question,
        candidates
    )

    print("\nTOP DATABASES")
    print("================================")

    for i, item in enumerate(
        ranked[:10],
        1
    ):

        print(
            f"{i}. "
            f"{item['database']} "
            f"(score={item['score']})"
        )