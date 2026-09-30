from pathlib import Path
import re
import pandas as pd

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings


# ==================================================
# 1. Paths
# ==================================================

BASE_DIR = Path("data/spiderman/databases")
CHROMA_DIR = "data/chroma"


# ==================================================
# 2. Extract one table schema
# ==================================================

def extract_table_schema(sql, database_name, table_name):

    pattern = (
        rf"CREATE TABLE\s+\`{re.escape(database_name)}\`\."
        rf"\`{re.escape(table_name)}\`\s*\((.*?)\)\s*;"
    )

    match = re.search(
        pattern,
        sql,
        re.IGNORECASE | re.DOTALL
    )

    if not match:
        return None

    table_body = match.group(1)

    columns = []
    primary_keys = []
    foreign_keys = []

    for line in table_body.splitlines():

        line = line.strip().rstrip(",")

        if not line:
            continue

        # --------------------------------------------------
        # Primary key
        # --------------------------------------------------

        pk_match = re.search(
            r"PRIMARY KEY\s*\((.*?)\)",
            line,
            re.IGNORECASE
        )

        if pk_match:

            keys = pk_match.group(1)

            primary_keys.extend(
                key.strip().strip("`")
                for key in keys.split(",")
            )

            continue

        # --------------------------------------------------
        # Foreign key
        # --------------------------------------------------

        fk_match = re.search(
            r"FOREIGN KEY\s*\(`?([^`]+)`?\)"
            r"\s+REFERENCES\s+"
            r"`?([^`.]+)`?\.`?([^`]+)`?"
            r"\s*\(\s*`?([^`]+)`?\s*\)",
            line,
            re.IGNORECASE
        )

        if fk_match:

            foreign_keys.append({
                "column": fk_match.group(1).strip(),
                "references_table": fk_match.group(3).strip(),
                "references_column": fk_match.group(4).strip()
            })

            continue

        # --------------------------------------------------
        # Column definition
        # --------------------------------------------------

        column_match = re.match(
            r"`([^`]+)`\s+(.+)",
            line
        )

        if column_match:

            columns.append({
                "name": column_match.group(1),
                "definition": column_match.group(2)
            })

    return {
        "database": database_name,
        "table": table_name,
        "columns": columns,
        "primary_keys": primary_keys,
        "foreign_keys": foreign_keys
    }


# ==================================================
# 3. Extract all tables from one database
# ==================================================

def extract_database_schema(db_dir):

    schema_file = db_dir / "schema.sql"

    if not schema_file.exists():
        return []

    sql = schema_file.read_text(
        encoding="utf-8"
    )

    database_name = db_dir.name

    table_matches = re.findall(
        rf"CREATE TABLE\s+\`{re.escape(database_name)}\`\."
        rf"\`([^`]+)\`",
        sql,
        re.IGNORECASE
    )

    tables = []

    for table_name in table_matches:

        table_schema = extract_table_schema(
            sql,
            database_name,
            table_name
        )

        if table_schema:
            tables.append(table_schema)

    return tables


# ==================================================
# 4. Extract sample values from CSV
# ==================================================

def extract_sample_values(
    db_dir,
    table_name,
    max_values=5
):

    data_dir = db_dir / "data"

    if not data_dir.exists():
        return {}

    csv_file = None

    # Find matching CSV case-insensitively
    for file in data_dir.glob("*.csv"):

        if file.stem.lower() == table_name.lower():

            csv_file = file
            break

    if csv_file is None:
        return {}

    try:

        df = pd.read_csv(
            csv_file,
            nrows=1000
        )

        samples = {}

        for column in df.columns:

            values = (
                df[column]
                .dropna()
                .astype(str)
                .drop_duplicates()
                .head(max_values)
                .tolist()
            )

            if values:
                samples[column] = values

        return samples

    except Exception as e:

        print(
            f"Warning: Could not read "
            f"{csv_file}: {e}"
        )

        return {}


# ==================================================
# 5. Convert schema + values into RAG document
# ==================================================

def create_document(
    table_schema,
    sample_values=None
):

    database = table_schema["database"]
    table = table_schema["table"]

    lines = []

    lines.append(
        f"DATABASE: {database}"
    )

    lines.append(
        f"TABLE: {table}"
    )

    # --------------------------------------------------
    # Columns
    # --------------------------------------------------

    lines.append(
        "COLUMNS:"
    )

    for column in table_schema["columns"]:

        lines.append(
            f"- {column['name']} "
            f"({column['definition']})"
        )

    # --------------------------------------------------
    # Primary keys
    # --------------------------------------------------

    if table_schema["primary_keys"]:

        lines.append(
            "PRIMARY KEYS: "
            + ", ".join(
                table_schema["primary_keys"]
            )
        )

    # --------------------------------------------------
    # Foreign keys
    # --------------------------------------------------

    if table_schema["foreign_keys"]:

        lines.append(
            "FOREIGN KEYS:"
        )

        for fk in table_schema["foreign_keys"]:

            lines.append(
                f"- {fk['column']} -> "
                f"{fk['references_table']}."
                f"{fk['references_column']}"
            )

    # --------------------------------------------------
    # Sample values
    # --------------------------------------------------

    if sample_values:

        lines.append(
            "SAMPLE VALUES:"
        )

        for column, values in sample_values.items():

            lines.append(
                f"- {column}: "
                + ", ".join(values)
            )

    return "\n".join(lines)


# ==================================================
# 6. Build all documents
# ==================================================

print("\n================================")
print("BUILDING VALUE-AWARE DOCUMENTS")
print("================================")


database_dirs = [
    path
    for path in BASE_DIR.iterdir()
    if path.is_dir()
]


documents = []
metadatas = []
ids = []


for db_dir in database_dirs:

    tables = extract_database_schema(
        db_dir
    )

    for table in tables:

        # Extract actual CSV values
        sample_values = extract_sample_values(
            db_dir,
            table["table"]
        )

        # Create RAG document
        document = create_document(
            table,
            sample_values
        )

        database = table["database"]
        table_name = table["table"]

        # Stable unique ID
        document_id = (
            f"{database}::{table_name}"
        )

        documents.append(document)

        metadatas.append({
            "database": database,
            "table": table_name
        })

        ids.append(document_id)


print(
    f"Databases processed: "
    f"{len(database_dirs)}"
)

print(
    f"Documents created: "
    f"{len(documents)}"
)


# ==================================================
# 7. Initialize embeddings
# ==================================================

print("\n================================")
print("LOADING EMBEDDING MODEL")
print("================================")


embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)


# ==================================================
# 8. Rebuild Chroma database
# ==================================================

print("\n================================")
print("REBUILDING CHROMA DATABASE")
print("================================")


vectorstore = Chroma(
    collection_name="spiderman_schema",
    persist_directory=CHROMA_DIR,
    embedding_function=embeddings
)


# --------------------------------------------------
# Delete old documents
# --------------------------------------------------

print("\nRemoving old documents...")


existing_data = vectorstore.get()

existing_ids = existing_data.get(
    "ids",
    []
)


if existing_ids:

    vectorstore.delete(
        ids=existing_ids
    )

    print(
        f"Deleted old documents: "
        f"{len(existing_ids)}"
    )

else:

    print(
        "No existing documents found."
    )


# ==================================================
# 9. Insert new documents
# ==================================================

print("\nAdding value-aware documents...")


vectorstore.add_texts(
    texts=documents,
    metadatas=metadatas,
    ids=ids
)


# ==================================================
# 10. Verify ingestion
# ==================================================

print("\n================================")
print("CHROMA INGESTION COMPLETE")
print("================================")


print(
    f"Documents inserted: "
    f"{len(documents)}"
)

print(
    "Chroma collection: "
    "spiderman_schema"
)


# ==================================================
# 11. Test retrieval
# ==================================================

test_question = (
    "Which students participate "
    "in Mountain Climbing?"
)


print("\n================================")
print("RETRIEVAL TEST")
print("================================")

print(
    f"Question: {test_question}"
)


results = vectorstore.similarity_search(
    test_question,
    k=10
)


print("\nRetrieved tables:")


for i, result in enumerate(
    results,
    1
):

    print(
        f"\n{i}. "
        f"{result.metadata['database']}::"
        f"{result.metadata['table']}"
    )

    print(
        result.page_content
    )