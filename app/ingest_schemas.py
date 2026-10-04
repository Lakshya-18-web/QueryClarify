from pathlib import Path
import re


# --------------------------------------------------
# 1. Locate SpiderMan databases
# --------------------------------------------------

BASE_DIR = Path("data/spiderman/databases")


# --------------------------------------------------
# 2. Extract one table schema
# --------------------------------------------------

def extract_table_schema(sql, database_name, table_name):

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

            column_name = column_match.group(1)
            column_definition = column_match.group(2)

            columns.append({
                "name": column_name,
                "definition": column_definition
            })

    return {
        "database": database_name,
        "table": table_name,
        "columns": columns,
        "primary_keys": primary_keys,
        "foreign_keys": foreign_keys
    }


# --------------------------------------------------
# 3. Extract all tables from one database
# --------------------------------------------------

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


# --------------------------------------------------
# 4. Test schema extraction
# --------------------------------------------------

if __name__ == "__main__":

    database_dirs = [
        path
        for path in BASE_DIR.iterdir()
        if path.is_dir()
    ]

    print("\n================================")
    print("SCHEMA EXTRACTION TEST")
    print("================================")

    print(
        f"Databases found: {len(database_dirs)}"
    )


    # --------------------------------------------------
    # Test first 3 databases
    # --------------------------------------------------

    for db_dir in database_dirs[:3]:

        print("\n--------------------------------")
        print(f"DATABASE: {db_dir.name}")
        print("--------------------------------")

        tables = extract_database_schema(
            db_dir
        )

        for table in tables:

            print(
                f"\nTABLE: {table['table']}"
            )

            print("COLUMNS:")

            for column in table["columns"]:

                print(
                    f"  - {column['name']} "
                    f"({column['definition']})"
                )

            print(
                f"PRIMARY KEYS: "
                f"{table['primary_keys']}"
            )

            print("FOREIGN KEYS:")

            for fk in table["foreign_keys"]:

                print(
                    f"  - {fk['column']} -> "
                    f"{fk['references_table']}."
                    f"{fk['references_column']}"
                )


    # --------------------------------------------------
    # 5. Validate all databases
    # --------------------------------------------------

    total_tables = 0
    databases_with_errors = []

    for db_dir in database_dirs:

        try:

            tables = extract_database_schema(
                db_dir
            )

            total_tables += len(tables)

            if len(tables) == 0:

                databases_with_errors.append(
                    db_dir.name
                )

        except Exception as e:

            databases_with_errors.append(
                f"{db_dir.name}: {e}"
            )


    # --------------------------------------------------
    # 6. Full dataset summary
    # --------------------------------------------------

    print("\n================================")
    print("FULL DATASET VALIDATION")
    print("================================")

    print(
        f"Databases checked: "
        f"{len(database_dirs)}"
    )

    print(
        f"Tables extracted: "
        f"{total_tables}"
    )

    print(
        f"Databases with errors: "
        f"{len(databases_with_errors)}"
    )

    if databases_with_errors:

        print("\nProblems:")

        for error in databases_with_errors:

            print(
                f"  - {error}"
            )