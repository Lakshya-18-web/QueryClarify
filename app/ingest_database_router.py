from pathlib import Path
import pandas as pd

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

from ingest_schemas import extract_database_schema


# ==================================================
# 1. Paths
# ==================================================

BASE_DIR = Path("data/spiderman/databases")
CHROMA_DIR = "data/chroma"

COLLECTION_NAME = "spiderman_databases"


# ==================================================
# 2. Embeddings
# ==================================================

embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)


# ==================================================
# 3. Extract sample values
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

    except Exception:

        return {}


# ==================================================
# 4. Convert database schema into searchable text
# ==================================================

def database_to_text(
    database_name,
    db_dir,
    tables
):

    lines = []

    lines.append(
        f"DATABASE: {database_name}"
    )

    lines.append("")

    for table in tables:

        table_name = table["table"]

        lines.append(
            f"TABLE: {table_name}"
        )

        # --------------------------------------------------
        # Columns
        # --------------------------------------------------

        if table["columns"]:

            lines.append(
                "COLUMNS:"
            )

            for column in table["columns"]:

                lines.append(
                    f"- {column['name']} "
                    f"({column['definition']})"
                )

        # --------------------------------------------------
        # Primary keys
        # --------------------------------------------------

        if table["primary_keys"]:

            lines.append(
                "PRIMARY KEYS: "
                + ", ".join(
                    table["primary_keys"]
                )
            )

        # --------------------------------------------------
        # Foreign keys
        # --------------------------------------------------

        if table["foreign_keys"]:

            lines.append(
                "FOREIGN KEYS:"
            )

            for fk in table["foreign_keys"]:

                lines.append(
                    f"- {fk['column']} -> "
                    f"{fk['references_table']}."
                    f"{fk['references_column']}"
                )

        # --------------------------------------------------
        # Sample values
        # --------------------------------------------------

        sample_values = extract_sample_values(
            db_dir,
            table_name
        )

        if sample_values:

            lines.append(
                "SAMPLE VALUES:"
            )

            for column, values in sample_values.items():

                lines.append(
                    f"- {column}: "
                    + ", ".join(values)
                )

        lines.append("")

    return "\n".join(lines)


# ==================================================
# 5. Main ingestion
# ==================================================

def main():

    documents = []
    metadatas = []
    ids = []

    database_count = 0
    total_tables = 0

    print("\n================================")
    print("VALUE-AWARE DATABASE ROUTER")
    print("================================\n")

    for db_dir in sorted(BASE_DIR.iterdir()):

        if not db_dir.is_dir():
            continue

        database_name = db_dir.name

        tables = extract_database_schema(
            db_dir
        )

        if not tables:
            continue

        database_text = database_to_text(
            database_name,
            db_dir,
            tables
        )

        documents.append(
            database_text
        )

        metadatas.append({
            "database": database_name,
            "table_count": len(tables)
        })

        ids.append(
            database_name
        )

        database_count += 1
        total_tables += len(tables)

    print(
        f"Databases found : "
        f"{database_count}"
    )

    print(
        f"Tables found    : "
        f"{total_tables}"
    )

    print("\nCreating Chroma collection...")

    vectorstore = Chroma(
        collection_name=COLLECTION_NAME,
        persist_directory=CHROMA_DIR,
        embedding_function=embeddings
    )

    # ==================================================
    # Remove previous database documents
    # ==================================================

    existing = vectorstore.get()

    existing_ids = existing.get(
        "ids",
        []
    )

    if existing_ids:

        print(
            f"Removing old documents: "
            f"{len(existing_ids)}"
        )

        vectorstore.delete(
            ids=existing_ids
        )

    # ==================================================
    # Insert new documents
    # ==================================================

    print(
        "\nAdding value-aware database documents..."
    )

    vectorstore.add_texts(
        texts=documents,
        metadatas=metadatas,
        ids=ids
    )

    print("\n================================")
    print("INGESTION COMPLETE")
    print("================================")

    print(
        f"Databases inserted : "
        f"{len(documents)}"
    )

    print(
        f"Collection         : "
        f"{COLLECTION_NAME}"
    )


# ==================================================
# 6. Run
# ==================================================

if __name__ == "__main__":
    main()