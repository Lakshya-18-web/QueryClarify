from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings


# ==================================================
# 1. Embeddings
# ==================================================

embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)


# ==================================================
# 2. Chroma
# ==================================================

vectorstore = Chroma(
    collection_name="spiderman_schema",
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

def schema_terms(schema):

    terms = set()

    for line in schema.splitlines():

        line = line.strip()

        # TABLE
        if line.startswith("TABLE:"):

            terms.update(
                tokenize(
                    line.replace("TABLE:", "")
                )
            )

        # COLUMNS
        elif line.startswith("-"):

            parts = line[1:].strip().split()

            if parts:

                terms.update(
                    tokenize(parts[0])
                )

        # FOREIGN KEYS
        elif "->" in line:

            left, right = line.split(
                "->",
                1
            )

            terms.update(tokenize(left))
            terms.update(tokenize(right))

    return terms


# ==================================================
# 5. Extract sample values
# ==================================================

def sample_value_terms(schema):

    values = []

    inside_sample_values = False

    for line in schema.splitlines():

        line = line.strip()

        if line == "SAMPLE VALUES:":
            inside_sample_values = True
            continue

        if inside_sample_values and line.startswith("-"):

            # Example:
            # - activity_name: Mountain Climbing, Canoeing

            if ":" in line:

                _, value_part = line.split(
                    ":",
                    1
                )

                values.append(
                    value_part.strip()
                )

    return tokenize(
        " ".join(values)
    )


# ==================================================
# 6. Extract actual sample value phrases
# ==================================================

def sample_value_phrases(schema):

    phrases = []

    inside_sample_values = False

    for line in schema.splitlines():

        line = line.strip()

        if line == "SAMPLE VALUES:":
            inside_sample_values = True
            continue

        if inside_sample_values and line.startswith("-"):

            if ":" in line:

                _, value_part = line.split(
                    ":",
                    1
                )

                # Split comma-separated values
                values = value_part.split(",")

                for value in values:

                    value = value.strip()

                    if value:
                        phrases.append(
                            value.lower()
                        )

    return phrases


# ==================================================
# 7. Calculate hybrid score
# ==================================================

def calculate_score(question, document):

    question_terms = tokenize(question)

    schema = document.page_content

    schema_lower = schema.lower()

    # --------------------------------------------------
    # Schema term overlap
    # --------------------------------------------------

    terms = schema_terms(schema)

    schema_overlap = (
        question_terms.intersection(terms)
    )

    lexical_score = len(
        schema_overlap
    )

    # --------------------------------------------------
    # Sample value matching
    # --------------------------------------------------

    value_terms = sample_value_terms(
        schema
    )

    value_overlap = (
        question_terms.intersection(
            value_terms
        )
    )

    value_score = len(
        value_overlap
    )

    # --------------------------------------------------
    # Exact multi-word value matching
    # --------------------------------------------------

    value_phrases = sample_value_phrases(
        schema
    )

    question_lower = question.lower()

    phrase_score = 0

    matched_phrases = []

    for phrase in value_phrases:

        # Ignore very short values
        if len(phrase) < 3:
            continue

        if phrase in question_lower:

            phrase_score += 20

            matched_phrases.append(
                phrase
            )

    # --------------------------------------------------
    # Relationship/schema structure bonus
    # --------------------------------------------------

    relationship_score = 0

    if (
        "foreign keys:" in schema_lower
        and len(question_terms) >= 2
    ):
        relationship_score += 1

    # --------------------------------------------------
    # Final score
    # --------------------------------------------------

    final_score = (
        lexical_score
        + (value_score * 3)
        + phrase_score
        + relationship_score
    )

    return (
        final_score,
        lexical_score,
        value_score,
        phrase_score,
        matched_phrases
    )


# ==================================================
# 8. Hybrid retrieval
# ==================================================

def hybrid_retrieve(question, k=50):

    # --------------------------------------------------
    # Semantic candidate pool
    # --------------------------------------------------

    candidates = vectorstore.similarity_search(
        question,
        k=k
    )

    ranked = []

    for document in candidates:

        (
            score,
            lexical_score,
            value_score,
            phrase_score,
            matched_phrases
        ) = calculate_score(
            question,
            document
        )

        ranked.append(
            (
                score,
                lexical_score,
                value_score,
                phrase_score,
                matched_phrases,
                document
            )
        )

    # --------------------------------------------------
    # Highest score first
    # --------------------------------------------------

    ranked.sort(
        key=lambda x: x[0],
        reverse=True
    )

    return ranked


# ==================================================
# 9. Test questions
# ==================================================

questions = [

    "Which students are participating in Mountain Climbing?",

    "How many teachers are there?",

    "How many documents do we have?",

    "How many singers do we have?",

    "Which players belong to each school?"

]


# ==================================================
# 10. Test hybrid retrieval
# ==================================================

for question in questions:

    print("\n================================")
    print("QUESTION")
    print("================================")

    print(question)

    results = hybrid_retrieve(
        question,
        k=50
    )

    print("\nTOP DATABASE/TABLE RESULTS")
    print("================================")

    for i, (
        score,
        lexical_score,
        value_score,
        phrase_score,
        matched_phrases,
        document
    ) in enumerate(
        results[:15],
        1
    ):

        database = document.metadata.get(
            "database"
        )

        table = document.metadata.get(
            "table"
        )

        print(
            f"{i}. "
            f"{database} :: {table} "
            f"(score={score}, "
            f"schema={lexical_score}, "
            f"values={value_score}, "
            f"phrases={phrase_score})"
        )

        if matched_phrases:

            print(
                f"   matched values: "
                f"{matched_phrases}"
            )