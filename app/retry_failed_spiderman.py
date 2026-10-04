
import pathlib
import sys

sys.path.insert(
    0,
    str(
        pathlib.Path(__file__)
        .resolve()
        .parent.parent
    )
)


import os

from sqlalchemy import text

from app.load_spiderman_mysql import (
    DATABASES_DIR,
    get_server_engine,
    load_database,
    quote_identifier,
)


FAILED_DATABASES = [
    "cre_Doc_Template_Mgt",
    "cre_Doc_Tracking_DB",
    "cre_Drama_Workshop_Groups",
    "cre_Theme_park",
    "flight_4",
    "hospital_1",
    "tracking_grants_for_research",
    "world_1",
]


def reset_databases():
    engine = get_server_engine()

    try:
        with engine.begin() as connection:
            for database in FAILED_DATABASES:
                print(f"Dropping: {database}")

                connection.execute(
                    text(
                        "DROP DATABASE IF EXISTS "
                        + quote_identifier(database)
                    )
                )
    finally:
        engine.dispose()


def main():
    print("=" * 70)
    print("RETRY FAILED SPIDERMAN DATABASES")
    print("=" * 70)

    reset_databases()

    results = []

    for database in FAILED_DATABASES:
        database_dir = os.path.join(
            DATABASES_DIR,
            database,
        )

        print("\n")
        print("=" * 70)
        print(f"RELOADING: {database}")
        print("=" * 70)

        try:
            ok, tables, rows = load_database(
                database_dir
            )

            results.append(
                (
                    database,
                    ok,
                    tables,
                    rows,
                    None,
                )
            )

        except Exception as exc:
            results.append(
                (
                    database,
                    False,
                    0,
                    0,
                    str(exc),
                )
            )

            print(
                f"\nFAILED: {database}"
            )
            print(exc)

    print("\n")
    print("=" * 70)
    print("RETRY SUMMARY")
    print("=" * 70)

    for database, ok, tables, rows, error in results:
        if ok:
            print(
                f"{database}: "
                f"SUCCESS | "
                f"{tables} tables | "
                f"{rows} rows"
            )
        else:
            print(
                f"{database}: FAILED | "
                f"{error}"
            )


if __name__ == "__main__":
    main()