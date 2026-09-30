from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings


# --------------------------------------------------
# Load embeddings
# --------------------------------------------------

embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)


# --------------------------------------------------
# Connect to Chroma
# --------------------------------------------------

vectorstore = Chroma(
    collection_name="spiderman_schema",
    persist_directory="data/chroma",
    embedding_function=embeddings
)


# --------------------------------------------------
# Search ONLY activity_1
# --------------------------------------------------

results = vectorstore.similarity_search(
    "Which students participate in Mountain Climbing?",
    k=5,
    filter={"database": "activity_1"}
)


# --------------------------------------------------
# Print results
# --------------------------------------------------

print("\n================================")
print("DATABASE-FILTERED RAG TEST")
print("================================")

for i, result in enumerate(results, 1):

    print(
        f"\n{i}. "
        f"{result.metadata['database']}::"
        f"{result.metadata['table']}"
    )

    print(result.page_content)