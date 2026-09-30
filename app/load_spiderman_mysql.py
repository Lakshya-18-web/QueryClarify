import os
import re
import sys
import pandas as pd

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError


BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)


ENV_FILE = os.path.join(
    BASE_DIR,
    ".env"
)

load_dotenv(ENV_FILE)


MYSQL_USER = os.getenv(
    "MYSQL_USER",
    "root"
)

MYSQL_PASSWORD = os.getenv(
    "MYSQL_PASSWORD",
    ""
)

MYSQL_HOST = os.getenv(
    "MYSQL_HOST",
    "localhost"
)

MYSQL_PORT = int(
    os.getenv(
        "MYSQL_PORT",
        "3306"
    )
)


SPIDERMAN_DIR = os.path.abspath(
    os.path.join(
        BASE_DIR,
        "..",
        "data",
        "spiderman"
    )
)

DATABASES_DIR = os.path.join(
    SPIDERMAN_DIR,
    "databases"
)


def quote_identifier(name):

    return "`" + name.replace(
        "`",
        "``"
    ) + "`"


def get_server_engine():

    return create_engine(
        f"mysql+pymysql://"
        f"{MYSQL_USER}:"
        f"{MYSQL_PASSWORD}@"
        f"{MYSQL_HOST}:"
        f"{MYSQL_PORT}"
    )


def get_database_engine(database):

    return create_engine(
        f"mysql+pymysql://"
        f"{MYSQL_USER}:"
        f"{MYSQL_PASSWORD}@"
        f"{MYSQL_HOST}:"
        f"{MYSQL_PORT}/"
        f"{database}"
    )


def split_sql_statements(sql):

    statements = []

    current = []

    in_single = False
    in_double = False
    in_backtick = False
    escape = False

    for char in sql:

        if escape:

            current.append(char)
            escape = False

            continue

        if char == "\\":

            current.append(char)
            escape = True

            continue

        if char == "'" and not in_double and not in_backtick:

            in_single = not in_single

        elif char == '"' and not in_single and not in_backtick:

            in_double = not in_double

        elif char == "`" and not in_single and not in_double:

            in_backtick = not in_backtick

        if (
            char == ";"
            and not in_single
            and not in_double
            and not in_backtick
        ):

            statement = "".join(
                current
            ).strip()

            if statement:

                statements.append(
                    statement
                )

            current = []

        else:

            current.append(char)

    statement = "".join(
        current
    ).strip()

    if statement:

        statements.append(
            statement
        )

    return statements


def remove_comments(sql):

    lines = []

    for line in sql.splitlines():

        stripped = line.strip()

        if stripped.startswith("--"):

            continue

        if stripped.startswith("#"):

            continue

        lines.append(line)

    return "\n".join(lines)


def clean_create_statement(
    statement,
    database
):

    statement = statement.strip()

    if not re.match(
        r"^\s*CREATE\s+TABLE",
        statement,
        flags=re.IGNORECASE
    ):

        return None

    statement = re.sub(
        r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS",
        "CREATE TABLE",
        statement,
        flags=re.IGNORECASE
    )

    statement = re.sub(
        r"CREATE\s+TABLE",
        "CREATE TABLE IF NOT EXISTS",
        statement,
        count=1,
        flags=re.IGNORECASE
    )

    qualified_pattern = re.compile(
        r"CREATE\s+TABLE\s+"
        r"(?:`[^`]+`|\w+)"
        r"\s*\.\s*"
        r"(?:`[^`]+`|\w+)",
        flags=re.IGNORECASE
    )

    match = qualified_pattern.search(
        statement
    )

    if match:

        table_part = match.group(0)

        table_match = re.search(
            r"\.\s*(`[^`]+`|\w+)",
            table_part
        )

        if table_match:

            table_name = (
                table_match
                .group(1)
                .strip("`")
            )

            replacement = (
                "CREATE TABLE IF NOT EXISTS "
                + quote_identifier(
                    table_name
                )
            )

            statement = (
                statement[:match.start()]
                + replacement
                + statement[match.end():]
            )

    return statement


def get_create_table_statements(
    schema_file,
    database
):

    with open(
        schema_file,
        "r",
        encoding="utf-8",
        errors="ignore"
    ) as f:

        sql = f.read()

    sql = remove_comments(
        sql
    )

    statements = split_sql_statements(
        sql
    )

    create_statements = []

    for statement in statements:

        cleaned = clean_create_statement(
            statement,
            database
        )

        if cleaned:

            create_statements.append(
                cleaned
            )

    return create_statements


def extract_table_name(
    create_statement
):

    pattern = re.compile(
        r"CREATE\s+TABLE\s+"
        r"(?:IF\s+NOT\s+EXISTS\s+)"
        r"(`[^`]+`|\w+)",
        flags=re.IGNORECASE
    )

    match = pattern.search(
        create_statement
    )

    if not match:

        return None

    return (
        match
        .group(1)
        .strip("`")
    )


def extract_foreign_key_dependencies(
    create_statement
):

    dependencies = []

    patterns = [

        re.compile(
            r"REFERENCES\s+"
            r"(?:`[^`]+`|\w+)"
            r"\s*\.\s*"
            r"(`[^`]+`|\w+)",
            flags=re.IGNORECASE
        ),

        re.compile(
            r"REFERENCES\s+"
            r"(`[^`]+`|\w+)",
            flags=re.IGNORECASE
        )

    ]

    for pattern in patterns:

        for match in pattern.finditer(
            create_statement
        ):

            table = (
                match
                .group(1)
                .strip("`")
            )

            if table not in dependencies:

                dependencies.append(
                    table
                )

    return dependencies


def order_create_statements(
    statements
):

    table_to_statement = {}

    for statement in statements:

        table = extract_table_name(
            statement
        )

        if table:

            table_to_statement[
                table
            ] = statement

    dependencies = {}

    for table, statement in (
        table_to_statement.items()
    ):

        deps = (
            extract_foreign_key_dependencies(
                statement
            )
        )

        deps = [
            dep
            for dep in deps
            if dep in table_to_statement
            and dep != table
        ]

        dependencies[
            table
        ] = deps

    ordered = []

    visited = set()

    visiting = set()

    def visit(table):

        if table in visited:

            return

        if table in visiting:

            return

        visiting.add(
            table
        )

        for dependency in (
            dependencies.get(
                table,
                []
            )
        ):

            visit(
                dependency
            )

        visiting.remove(
            table
        )

        visited.add(
            table
        )

        ordered.append(
            table
        )

    for table in table_to_statement:

        visit(
            table
        )

    return [
        table_to_statement[table]
        for table in ordered
    ]


def create_database(
    database
):

    engine = get_server_engine()

    try:

        with engine.begin() as connection:

            connection.execute(
                text(
                    f"""
                    CREATE DATABASE IF NOT EXISTS
                    {quote_identifier(database)}
                    """
                )
            )

    finally:

        engine.dispose()


def create_tables(
    database,
    statements
):

    engine = get_database_engine(
        database
    )

    try:

        with engine.begin() as connection:

            connection.execute(
                text(
                    "SET FOREIGN_KEY_CHECKS = 0"
                )
            )

            for statement in statements:

                table_name = (
                    extract_table_name(
                        statement
                    )
                )

                print(
                    f"    Creating table: "
                    f"{table_name}"
                )

                connection.execute(
                    text(statement)
                )

            connection.execute(
                text(
                    "SET FOREIGN_KEY_CHECKS = 1"
                )
            )

    finally:

        engine.dispose()


def find_csv_for_table(
    database_dir,
    table_name
):

    data_dir = os.path.join(
        database_dir,
        "data"
    )

    if not os.path.isdir(
        data_dir
    ):

        return None

    candidates = os.listdir(
        data_dir
    )

    exact_name = (
        f"{table_name}.csv"
    )

    if exact_name in candidates:

        return os.path.join(
            data_dir,
            exact_name
        )

    lower_map = {
        filename.lower(): filename
        for filename in candidates
    }

    if (
        exact_name.lower()
        in lower_map
    ):

        return os.path.join(
            data_dir,
            lower_map[
                exact_name.lower()
            ]
        )

    normalized_table = re.sub(
        r"[^a-z0-9]",
        "",
        table_name.lower()
    )

    for filename in candidates:

        if not filename.lower().endswith(
            ".csv"
        ):

            continue

        filename_table = (
            filename[:-4]
        )

        normalized_file = re.sub(
            r"[^a-z0-9]",
            "",
            filename_table.lower()
        )

        if (
            normalized_file
            == normalized_table
        ):

            return os.path.join(
                data_dir,
                filename
            )

    return None


def clean_dataframe(
    df
):

    df = df.copy()

    df.columns = [
        str(column).strip()
        for column in df.columns
    ]

    for column in df.columns:

        if df[column].dtype == "object":

            df[column] = df[column].apply(
                lambda value:
                    None
                    if pd.isna(value)
                    or str(value)
                    .strip()
                    .lower()
                    in {
                        "",
                        "null",
                        "none",
                        "nan",
                        "na",
                        "n/a"
                    }
                    else value
            )

    return df


def load_csv(
    database,
    table_name,
    csv_file
):

    print(
        f"    Loading: "
        f"{os.path.basename(csv_file)}"
        f" -> {table_name}"
    )

    df = pd.read_csv(
        csv_file,
        low_memory=False
    )

    df = clean_dataframe(
        df
    )

    if df.empty:

        print(
            "      0 rows"
        )

        return 0

    engine = get_database_engine(
        database
    )

    try:

        with engine.begin() as connection:

            connection.execute(
                text(
                    "SET FOREIGN_KEY_CHECKS = 0"
                )
            )

            df.to_sql(
                name=table_name,
                con=connection,
                if_exists="append",
                index=False,
                chunksize=1000,
                method="multi"
            )

            connection.execute(
                text(
                    "SET FOREIGN_KEY_CHECKS = 1"
                )
            )

    finally:

        engine.dispose()

    return len(df)


def load_database(
    database_dir
):

    database = os.path.basename(
        database_dir
    )

    schema_file = os.path.join(
        database_dir,
        "schema.sql"
    )

    if not os.path.isfile(
        schema_file
    ):

        print(
            f"\nSKIPPING {database}"
        )

        print(
            "schema.sql not found."
        )

        return False, 0, 0

    print(
        "\n"
        + "=" * 70
    )

    print(
        f"DATABASE: {database}"
    )

    print(
        "=" * 70
    )

    create_database(
        database
    )

    statements = (
        get_create_table_statements(
            schema_file,
            database
        )
    )

    if not statements:

        print(
            "No CREATE TABLE statements found."
        )

        return False, 0, 0

    ordered_statements = (
        order_create_statements(
            statements
        )
    )

    print(
        f"Tables in schema: "
        f"{len(ordered_statements)}"
    )

    create_tables(
        database,
        ordered_statements
    )

    loaded_tables = 0

    loaded_rows = 0

    for statement in ordered_statements:

        table_name = (
            extract_table_name(
                statement
            )
        )

        if not table_name:

            continue

        csv_file = (
            find_csv_for_table(
                database_dir,
                table_name
            )
        )

        if csv_file is None:

            print(
                f"    No CSV found for "
                f"{table_name}"
            )

            continue

        try:

            rows = load_csv(
                database,
                table_name,
                csv_file
            )

            loaded_tables += 1

            loaded_rows += rows

            print(
                f"      {rows} rows"
            )

        except Exception as e:

            print(
                f"\n    ERROR loading "
                f"{database}.{table_name}"
            )

            print(
                e
            )

            raise

    return (
        True,
        loaded_tables,
        loaded_rows
    )


def main():

    print(
        "=" * 70
    )

    print(
        "SPIDERMAN -> MYSQL LOADER"
    )

    print(
        "=" * 70
    )

    print(
        f"\n.env file:"
    )

    print(
        ENV_FILE
    )

    print(
        f"\nSpiderMan directory:"
    )

    print(
        SPIDERMAN_DIR
    )

    print(
        f"\nDatabases directory:"
    )

    print(
        DATABASES_DIR
    )

    if not os.path.isfile(
        ENV_FILE
    ):

        print(
            "\nERROR: .env file not found."
        )

        sys.exit(1)

    if not os.path.isdir(
        DATABASES_DIR
    ):

        print(
            "\nERROR: SpiderMan databases "
            "directory not found."
        )

        sys.exit(1)

    if not MYSQL_PASSWORD:

        print(
            "\nERROR: MYSQL_PASSWORD "
            "was not loaded from .env."
        )

        print(
            "\nMake sure your app/.env contains:"
        )

        print(
            "MYSQL_USER=root"
        )

        print(
            "MYSQL_PASSWORD=YOUR_PASSWORD"
        )

        print(
            "MYSQL_HOST=localhost"
        )

        print(
            "MYSQL_PORT=3306"
        )

        sys.exit(1)

    print(
        "\nMySQL configuration:"
    )

    print(
        f"User: {MYSQL_USER}"
    )

    print(
        f"Host: {MYSQL_HOST}"
    )

    print(
        f"Port: {MYSQL_PORT}"
    )

    print(
        "Password: [loaded]"
    )

    database_dirs = sorted(
        [
            os.path.join(
                DATABASES_DIR,
                name
            )
            for name in os.listdir(
                DATABASES_DIR
            )
            if os.path.isdir(
                os.path.join(
                    DATABASES_DIR,
                    name
                )
            )
        ]
    )

    print(
        f"\nDatabases found: "
        f"{len(database_dirs)}"
    )

    if not database_dirs:

        print(
            "No SpiderMan databases found."
        )

        sys.exit(1)

    response = input(
        "\nContinue? [y/N]: "
    ).strip().lower()

    if response != "y":

        print(
            "Cancelled."
        )

        return

    successful = 0

    failed = 0

    total_tables = 0

    total_rows = 0

    failed_databases = []

    for index, database_dir in enumerate(
        database_dirs,
        start=1
    ):

        database = os.path.basename(
            database_dir
        )

        print(
            f"\n[{index}/{len(database_dirs)}]"
        )

        try:

            ok, tables, rows = (
                load_database(
                    database_dir
                )
            )

            if ok:

                successful += 1

                total_tables += tables

                total_rows += rows

        except Exception as e:

            failed += 1

            failed_databases.append(
                database
            )

            print(
                f"\nFAILED DATABASE: "
                f"{database}"
            )

            print(
                e
            )

            print(
                "\nContinuing with next database..."
            )

    print(
        "\n"
        + "=" * 70
    )

    print(
        "LOAD COMPLETE"
    )

    print(
        "=" * 70
    )

    print(
        f"Successful databases : "
        f"{successful}"
    )

    print(
        f"Failed databases     : "
        f"{failed}"
    )

    print(
        f"Tables loaded        : "
        f"{total_tables}"
    )

    print(
        f"Rows loaded          : "
        f"{total_rows}"
    )

    if failed_databases:

        print(
            "\nFailed database names:"
        )

        for database in failed_databases:

            print(
                f"  - {database}"
            )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()