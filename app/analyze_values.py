from collections import Counter

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings


# ==================================================
# 1. Load Chroma
# ==================================================

embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)

vectorstore = Chroma(
    collection_name="spiderman_schema",
    persist_directory="data/chroma",
    embedding_function=embeddings
)


# ==================================================
# 2. Get all documents
# ==================================================

data = vectorstore.get(
    include=["documents", "metadatas"]
)

documents = data["documents"]
metadatas = data["metadatas"]


# ==================================================
# 3. Count values across documents
# ==================================================

value_frequency = Counter()


for document in documents:

    inside_values = False

    for line in document.splitlines():

        line = line.strip()

        if line == "SAMPLE VALUES:":
            inside_values = True
            continue

        if inside_values and line.startswith("-"):

            if ":" not in line:
                continue

            _, value_part = line.split(
                ":",
                1
            )

            values = value_part.split(",")

            for value in values:

                value = value.strip().lower()

                if value:
                    value_frequency[value] += 1


# ==================================================
# 4. Show most common values
# ==================================================

print("\n================================")
print("MOST COMMON SAMPLE VALUES")
print("================================")

for value, count in value_frequency.most_common(30):

    print(
        f"{count:4}  {value}"
    )


# ==================================================
# 5. Check important values
# ==================================================

print("\n================================")
print("IMPORTANT VALUE FREQUENCIES")
print("================================")

for value in [
    "document",
    "player",
    "student",
    "teacher",
    "school",
    "singer",
    "mountain climbing"
]:

    print(
        f"{value:20} -> "
        f"{value_frequency.get(value, 0)} documents"
    )