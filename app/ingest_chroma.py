from pathlib import Path
import hashlib
import math
import re

import pandas as pd
import chromadb


# ==================================================
# 1. Paths
# ==================================================

BASE_DIR = Path("data/spiderman/databases")
CHROMA_DIR = "data/chroma"

# Keep the same dimensionality as all-MiniLM-L6-v2.
# We generate deterministic lightweight embeddings ourselves.
EMBEDDING_DIM = 384


# ==================================================
# 2. Lightweight deterministic embedding
# ==================================================

def generate_embedding(text: str, dimension: int = EMBEDDING_DIM):
    """
    Generate a deterministic lightweight embedding without
    downloading or loading any ML model.

    This is intentionally used only so Chroma has valid
    vector representations. QueryClarify's production RAG
    currently retrieves records with .get() and performs
    its own schema/table scoring.
    """

    vector = [0.0] * dimension

    # Tokenize text.
    tokens = re.findall(
        r"[a-zA-Z0-9_]+",
        text.lower()
    )

    if not tokens:
        return vector

    for token in tokens:

        digest = hashlib.sha256(
            token.encode("utf-8")
        ).digest()

        # Generate several deterministic positions
        # from the SHA-256 digest.
        for offset in range(0, len(digest), 4):

            chunk = digest[offset:offset + 4]

            if len(chunk) < 4:
                continue

            index = int.from_bytes(
                chunk,
                byteorder="little"
            ) % dimension

            sign = (
                1.0
                if digest[offset] % 2 == 0
                else -1.0
            )

            vector[index] += sign

    # Normalize vector.
    magnitude = math.sqrt(
        sum(value * value for value in vector)
    )

    if magnitude > 0:

        vector = [
            value / magnitude
            for value in vector
        ]

    return vector


# ==================================================
# 3. Extract one table schema
# ==================================================

def extract_table_schema(
    sql,
    database_name,
    table_name
):

    pattern = (
        rf"CREATE TABLE\s+`{re.escape(database_name)}`\."
        rf"`{re.escape(table_name)}`\s*\((.*?)\)\s*;"
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
# 4. Extract all tables from one database
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
        rf"CREATE TABLE\s+`{re.escape(database_name)}`\."
        rf"`([^`]+)`",
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
# 5. Extract sample values from CSV
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

    # Find matching CSV case-insensitively.
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
# 6. Convert schema + values into RAG document
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
# 7. Build all documents
# ==================================================

print("\n================================")
print("BUILDING PRODUCTION RAG INDEX")
print("================================")


if not BASE_DIR.exists():

    raise FileNotFoundError(
        f"SpiderMan database directory not found: "
        f"{BASE_DIR}"
    )


database_dirs = sorted(
    [
        path
        for path in BASE_DIR.iterdir()
        if path.is_dir()
    ],
    key=lambda p: p.name.lower()
)


documents = []
metadatas = []
ids = []
embeddings = []

database_documents = []
database_metadatas = []
database_ids = []
database_embeddings = []


print(
    f"Databases discovered: "
    f"{len(database_dirs)}"
)


# ==================================================
# 8. Process databases
# ==================================================

for db_index, db_dir in enumerate(
    database_dirs,
    start=1
):

    database_name = db_dir.name

    tables = extract_database_schema(
        db_dir
    )

    if not tables:

        print(
            f"WARNING: No tables found in "
            f"{database_name}"
        )

    # --------------------------------------------------
    # Database routing document
    # --------------------------------------------------

    database_document_lines = [
        f"DATABASE: {database_name}",
        "TABLES:"
    ]

    for table in tables:

        database_document_lines.append(
            f"- {table['table']}"
        )

    database_document = "\n".join(
        database_document_lines
    )

    database_documents.append(
        database_document
    )

    database_metadatas.append({
        "database": database_name
    })

    database_ids.append(
        database_name
    )

    database_embeddings.append(
        generate_embedding(
            database_document
        )
    )

    # --------------------------------------------------
    # Table documents
    # --------------------------------------------------

    for table in tables:

        table_name = table["table"]

        sample_values = extract_sample_values(
            db_dir,
            table_name
        )

        document = create_document(
            table,
            sample_values
        )

        document_id = (
            f"{database_name}::{table_name}"
        )

        documents.append(
            document
        )

        metadatas.append({
            "database": database_name,
            "table": table_name
        })

        ids.append(
            document_id
        )

        embeddings.append(
            generate_embedding(
                document
            )
        )

    # Progress every 25 databases.
    if (
        db_index == 1
        or db_index % 25 == 0
        or db_index == len(database_dirs)
    ):

        print(
            f"Processed "
            f"{db_index}/{len(database_dirs)} "
            f"databases..."
        )


print(
    f"Databases processed: "
    f"{len(database_dirs)}"
)

print(
    f"Table documents created: "
    f"{len(documents)}"
)

print(
    f"Database documents created: "
    f"{len(database_documents)}"
)


# ==================================================
# 9. Initialize Chroma
# ==================================================

print("\n================================")
print("INITIALIZING CHROMA")
print("================================")

print(
    "Embedding model: NONE"
)

print(
    "Generating lightweight deterministic vectors..."
)

print(
    f"Embedding dimension: "
    f"{EMBEDDING_DIM}"
)


client = chromadb.PersistentClient(
    path=CHROMA_DIR
)


# ==================================================
# 10. Rebuild schema collection
# ==================================================

print("\nRebuilding spiderman_schema...")


try:

    client.delete_collection(
        name="spiderman_schema"
    )

except Exception:
    pass


schema_collection = client.get_or_create_collection(
    name="spiderman_schema",
    metadata={
        "description":
            "SpiderMan table schemas for QueryClarify",
        "embedding_dimension":
            EMBEDDING_DIM
    }
)


# ==================================================
# 11. Insert schema documents
# ==================================================

print(
    f"Adding {len(documents)} table documents..."
)


if documents:

    schema_collection.add(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings
    )


# ==================================================
# 12. Rebuild database collection
# ==================================================

print("\nRebuilding spiderman_databases...")


try:

    client.delete_collection(
        name="spiderman_databases"
    )

except Exception:
    pass


database_collection = client.get_or_create_collection(
    name="spiderman_databases",
    metadata={
        "description":
            "SpiderMan database routing metadata",
        "embedding_dimension":
            EMBEDDING_DIM
    }
)


# ==================================================
# 13. Insert database documents
# ==================================================

print(
    f"Adding "
    f"{len(database_documents)} "
    f"database documents..."
)


if database_documents:

    database_collection.add(
        ids=database_ids,
        documents=database_documents,
        metadatas=database_metadatas,
        embeddings=database_embeddings
    )


# ==================================================
# 14. Verify ingestion
# ==================================================

print("\n================================")
print("CHROMA INGESTION COMPLETE")
print("================================")


schema_count = (
    schema_collection.count()
)

database_count = (
    database_collection.count()
)


print(
    f"Schema documents: "
    f"{schema_count}"
)

print(
    f"Database documents: "
    f"{database_count}"
)

print(
    f"Expected databases: "
    f"{len(database_dirs)}"
)

print(
    f"Expected tables: "
    f"{len(documents)}"
)


# ==================================================
# 15. Validate counts
# ==================================================

if database_count != len(database_dirs):

    raise RuntimeError(
        "Database collection count mismatch: "
        f"expected {len(database_dirs)}, "
        f"got {database_count}"
    )


if schema_count != len(documents):

    raise RuntimeError(
        "Schema collection count mismatch: "
        f"expected {len(documents)}, "
        f"got {schema_count}"
    )


# ==================================================
# 16. Test database retrieval
# ==================================================

print("\n================================")
print("DATABASE ROUTING TEST")
print("================================")


test_database = (
    database_collection.get(
        ids=["college_3"]
    )
)


print(
    "college_3 found:",
    bool(
        test_database.get("ids")
    )
)


# ==================================================
# 17. Test schema retrieval
# ==================================================

print("\n================================")
print("SCHEMA RETRIEVAL TEST")
print("================================")


test_schema = (
    schema_collection.get(
        ids=[
            "college_3::Student",
            "college_3::Course",
            "college_3::Enrolled_in"
        ]
    )
)


print(
    "Retrieved schema documents:",
    len(
        test_schema.get(
            "ids",
            []
        )
    )
)


# ==================================================
# 18. Final verification
# ==================================================

print("\n================================")
print("PRODUCTION RAG INDEX READY")
print("================================")


print(
    f"157-DB target: "
    f"{database_count}/{len(database_dirs)}"
)

print(
    f"Table target: "
    f"{schema_count}/{len(documents)}"
)

print(
    "Embedding model: NONE"
)

print(
    "HuggingFace: DISABLED"
)

print(
    "ONNX: DISABLED"
)

print(
    "sentence-transformers: DISABLED"
)

print(
    "================================"
)