
import os
from pathlib import Path

from langchain_chroma import Chroma
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.ingest_schemas import extract_database_schema
from dotenv import load_dotenv
load_dotenv()


BASE_DIR = Path("data/spiderman/databases")
CHROMA_DIR = "data/chroma"
COLLECTION_NAME = "spiderman_databases_gemini"
MODEL_NAME = "gemini-embedding-001"


def database_to_text(database_name: str, tables: list[dict]) -> str:
    """Convert a database schema into searchable text."""
    lines = [f"DATABASE: {database_name}"]

    for table in tables:
        lines.append(f"TABLE: {table['table']}")

        columns = table.get("columns", [])
        if columns:
            lines.append("COLUMNS:")
            for column in columns:
                lines.append(
                    f"- {column['name']} ({column['definition']})"
                )

        primary_keys = table.get("primary_keys", [])
        if primary_keys:
            lines.append("PRIMARY KEYS: " + ", ".join(primary_keys))

        foreign_keys = table.get("foreign_keys", [])
        if foreign_keys:
            lines.append("FOREIGN KEYS:")
            for fk in foreign_keys:
                lines.append(
                    f"- {fk['column']} -> "
                    f"{fk['references_table']}."
                    f"{fk['references_column']}"
                )

        lines.append("")

    return "\n".join(lines)


def main() -> None:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY is not configured.")

    if not BASE_DIR.is_dir():
        raise FileNotFoundError(
            f"Dataset directory not found: {BASE_DIR}"
        )

    documents = []
    metadatas = []
    ids = []

    for db_dir in sorted(BASE_DIR.iterdir()):
        if not db_dir.is_dir():
            continue

        tables = extract_database_schema(db_dir)
        if not tables:
            continue

        documents.append(database_to_text(db_dir.name, tables))
        metadatas.append({
            "database": db_dir.name,
            "table_count": len(tables),
        })
        ids.append(db_dir.name)

    if not documents:
        raise RuntimeError(
            f"No database schemas found in {BASE_DIR}; nothing was indexed."
        )

    print(f"Prepared {len(documents)} database documents.")

    embeddings = GoogleGenerativeAIEmbeddings(
        model=MODEL_NAME,
        google_api_key=api_key,
    )

    vectorstore = Chroma(
        collection_name=COLLECTION_NAME,
        persist_directory=CHROMA_DIR,
        embedding_function=embeddings,
    )

    # Upsert into the Gemini collection only; preserve the legacy collection.
    vectorstore._collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
    )

    indexed_count = vectorstore._collection.count()
    print(f"Collection: {COLLECTION_NAME}")
    print(f"Indexed entries: {indexed_count}")

    if indexed_count < len(documents):
        raise RuntimeError(
            f"Expected at least {len(documents)} entries, "
            f"but found {indexed_count}."
        )

    print("Gemini router ingestion completed successfully.")


if __name__ == "__main__":
    main()
