from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings


# ==================================================
# 1. Embeddings
# ==================================================

embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)


# ==================================================
# 2. Database router vectorstore
# ==================================================

vectorstore = Chroma(
    collection_name="spiderman_databases",
    persist_directory="data/chroma",
    embedding_function=embeddings
)


# ==================================================
# 3. Tokenizer
# ==================================================

def tokenize(text):

    text = text.lower()

    for char in "?,.!'\"():;_-":
        text = text.replace(char, " ")

    return set(text.split())


# ==================================================
# 4. Extract schema terms
# ==================================================

def schema_terms(document):

    terms = set()

    for line in document.splitlines():

        line = line.strip()

        if line.startswith("TABLE:"):

            terms.update(
                tokenize(
                    line.replace("TABLE:", "")
                )
            )

        elif line.startswith("-"):

            if ":" in line:
                left = line.split(":", 1)[0]
            else:
                left = line

            terms.update(
                tokenize(
                    left.replace("-", "")
                )
            )

    return terms


# ==================================================
# 5. Extract sample values
# ==================================================

def sample_values(document):

    values = []

    inside_values = False

    for line in document.splitlines():

        line = line.strip()

        if line == "SAMPLE VALUES:":
            inside_values = True
            continue

        if not inside_values:
            continue

        if line.startswith("-") and ":" in line:

            _, value_part = line.split(
                ":",
                1
            )

            for value in value_part.split(","):

                value = value.strip().lower()

                if value:
                    values.append(value)

    return values


# ==================================================
# 6. Calculate database score
# ==================================================

def calculate_score(question, document):

    question_lower = question.lower()

    question_terms = tokenize(
        question
    )

    # --------------------------------------------------
    # Schema matching
    # --------------------------------------------------

    terms = schema_terms(
        document
    )

    schema_overlap = (
        question_terms.intersection(terms)
    )

    schema_score = len(
        schema_overlap
    )

    # --------------------------------------------------
    # Value matching
    # --------------------------------------------------

    values = sample_values(
        document
    )

    value_score = 0
    matched_values = []

    for value in values:

        value_terms = tokenize(value)

        overlap = (
            question_terms.intersection(
                value_terms
            )
        )

        value_score += len(overlap)

        if len(value) >= 4 and value in question_lower:

            # Stronger score for multi-word /
            # specific values.
            if len(value.split()) >= 2:

                value_score += 15

            else:

                value_score += 2

            matched_values.append(
                value
            )

    # --------------------------------------------------
    # Table combination evidence
    # --------------------------------------------------

    tables = []

    for line in document.splitlines():

        line = line.strip()

        if line.startswith("TABLE:"):

            table_name = (
                line.replace(
                    "TABLE:",
                    ""
                ).strip()
            )

            tables.append(
                table_name.lower()
            )

    relationship_score = 0

    # Questions involving relationships
    # should benefit from databases containing
    # multiple relevant tables.

    relationship_words = {
        "participate",
        "participating",
        "belong",
        "taken",
        "enrolled",
        "related",
        "between",
        "each"
    }

    if question_terms.intersection(
        relationship_words
    ):

        relevant_table_count = 0

        for table in tables:

            table_terms = tokenize(
                table
            )

            if question_terms.intersection(
                table_terms
            ):

                relevant_table_count += 1

        relationship_score += min(
            relevant_table_count,
            3
        )

    # --------------------------------------------------
    # Final score
    # --------------------------------------------------

    final_score = (
        schema_score
        + value_score
        + relationship_score
    )

    return (
        final_score,
        schema_score,
        value_score,
        relationship_score,
        matched_values
    )


# ==================================================
# 7. Hybrid database retrieval
# ==================================================

def hybrid_database_retrieve(
    question,
    k=50
):

    # --------------------------------------------------
    # Semantic candidate generation
    # --------------------------------------------------

    candidates = vectorstore.similarity_search(
        question,
        k=k
    )

    ranked = []

    for document in candidates:

        (
            score,
            schema_score,
            value_score,
            relationship_score,
            matched_values
        ) = calculate_score(
            question,
            document.page_content
        )

        ranked.append(
            (
                score,
                schema_score,
                value_score,
                relationship_score,
                matched_values,
                document
            )
        )

    # --------------------------------------------------
    # Rank databases
    # --------------------------------------------------

    ranked.sort(
        key=lambda x: x[0],
        reverse=True
    )

    return ranked


# ==================================================
# 8. Test questions
# ==================================================

questions = [

    "Which students are participating in Mountain Climbing?",

    "Show me the courses taken by students.",

    "Which players belong to each school?"

]


# ==================================================
# 9. Test router
# ==================================================

for question in questions:

    print("\n================================")
    print("QUESTION")
    print("================================")

    print(question)

    results = hybrid_database_retrieve(
        question,
        k=50
    )

    print("\nTOP DATABASES")
    print("================================")

    for i, (
        score,
        schema_score,
        value_score,
        relationship_score,
        matched_values,
        document
    ) in enumerate(
        results[:10],
        1
    ):

        database = document.metadata.get(
            "database"
        )

        table_count = document.metadata.get(
            "table_count"
        )

        print(
            f"{i}. {database} "
            f"({table_count} tables) "
            f"[score={score}, "
            f"schema={schema_score}, "
            f"values={value_score}, "
            f"relations={relationship_score}]"
        )

        if matched_values:

            print(
                f"   matched values: "
                f"{matched_values}"
            )