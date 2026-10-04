import pathlib
import sys

# Make `app` importable when run as `python app/<script>.py` from the repo root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import os
import re
from pathlib import Path

import pandas as pd
import pymysql
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect

from app.admin_credentials import admin_settings, mysql_url, pymysql_kwargs
from app.secrets_redaction import redact

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "app" / ".env")

# ADMIN tool (creates schemas, loads data): credentials come from
# MYSQL_ADMIN_USER / MYSQL_ADMIN_PASSWORD (or the legacy MYSQL_USER /
# MYSQL_PASSWORD) in app/.env. Nothing is hard-coded.
SETTINGS = admin_settings()
BASE = ROOT / "data" / "spiderman" / "databases"

DATABASES = [
    "world_1", "flight_2", "cre_Doc_Template_Mgt", "dog_kennels",
    "student_transcripts_tracking", "wta_1", "tvshow", "pets_1",
    "poker_player", "concert_singer", "employee_hire_evaluation",
    "singer", "network_1", "course_teach", "orchestra",
    "museum_visit", "voter_1", "real_estate_properties",
]

def connection(database=None):
    # local_infile is required by the bulk loader below. This is an OFFLINE
    # admin tool; never reuse this connection helper in the web application.
    return pymysql.connect(
        **pymysql_kwargs(
            SETTINGS, database, autocommit=True, local_infile=True
        )
    )

def create_schema(db, schema_file):
    conn = connection(db)
    try:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            sql = schema_file.read_text(encoding="utf-8")
            sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
            sql = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)

            statements = [x.strip() for x in sql.split(";") if x.strip()]
            created = 0

            for statement in statements:
                if not re.search(r"\bCREATE\s+TABLE\b", statement, re.I):
                    continue

                statement = re.sub(
                    rf"`{re.escape(db)}`\s*\.",
                    "",
                    statement,
                    flags=re.I,
                )
                statement = re.sub(
                    rf"\b{re.escape(db)}\s*\.",
                    "",
                    statement,
                    flags=re.I,
                )

                try:
                    cur.execute(statement)
                    created += 1
                except Exception as e:
                    print(f"  Schema warning: {redact(e)}")

            cur.execute("SET FOREIGN_KEY_CHECKS=1")
            print(f"Tables created from schema: {created}")
    finally:
        conn.close()

def find_csv(db_path, table):
    data_dir = db_path / "data"
    if not data_dir.exists():
        return None

    exact = data_dir / f"{table}.csv"
    if exact.exists():
        return exact

    for file in data_dir.glob("*.csv"):
        if file.stem.lower() == table.lower():
            return file
    return None

def load_table(db, table, csv_file, inspector):
    print(f"  Loading {table} from {csv_file.name}")

    try:
        df = pd.read_csv(
            csv_file,
            dtype=str,
            keep_default_na=False,
            na_filter=False,
        )
    except Exception as e:
        print(f"  ERROR reading {csv_file.name}: {redact(e)}")
        return False

    schema_columns = [
        column["name"] for column in inspector.get_columns(table)
    ]

    missing = [c for c in schema_columns if c not in df.columns]
    if missing:
        print(f"  ERROR {table}: missing columns {missing}")
        return False

    df = df[schema_columns]
    df = df.replace(["NULL", "null", "None", "none"], None)

    rows = [
        tuple(row)
        for row in df.itertuples(index=False, name=None)
    ]

    if not rows:
        print(f"  {table}: 0 rows")
        return True

    conn = connection(db)
    try:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=0")

            columns = ", ".join(f"`{c}`" for c in schema_columns)
            placeholders = ", ".join(["%s"] * len(schema_columns))
            sql = f"INSERT INTO `{table}` ({columns}) VALUES ({placeholders})"

            try:
                cur.executemany(sql, rows)
                print(f"  {table}: {len(rows)} rows loaded")
                return True
            except Exception as e:
                print(f"  ERROR loading {table}: {redact(e)}")
                return False
            finally:
                cur.execute("SET FOREIGN_KEY_CHECKS=1")
    finally:
        conn.close()

def load_database(db, db_path):
    url = mysql_url(SETTINGS, db)
    engine = create_engine(url)

    try:
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        print(f"Tables from schema: {tables}")

        success = 0
        failed = 0

        for table in tables:
            csv_file = find_csv(db_path, table)

            if csv_file is None:
                print(f"  WARNING: CSV not found for {table}")
                failed += 1
                continue

            if load_table(db, table, csv_file, inspector):
                success += 1
            else:
                failed += 1

        print(f"Database result: {success} tables loaded, {failed} failed")
    finally:
        engine.dispose()

def repair_database(db, root_connection):
    print("\n" + "=" * 60)
    print(f"REPAIRING: {db}")
    print("=" * 60)

    db_path = BASE / db
    schema_file = db_path / "schema.sql"

    if not db_path.exists():
        print(f"ERROR: database directory does not exist: {db_path}")
        return

    if not schema_file.exists():
        print(f"ERROR: schema.sql missing: {schema_file}")
        return

    try:
        with root_connection.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{db}`")
            cur.execute(f"CREATE DATABASE `{db}`")
        print("Database recreated.")
    except Exception as e:
        print(f"ERROR recreating {db}: {redact(e)}")
        return

    try:
        create_schema(db, schema_file)
        load_database(db, db_path)
    except Exception as e:
        print(f"ERROR loading database: {redact(e)}")

def main():
    print(f"Databases to repair: {len(DATABASES)}")
    root_connection = connection()

    try:
        for db in DATABASES:
            repair_database(db, root_connection)
    finally:
        root_connection.close()

    print("\n" + "=" * 60)
    print("REPAIR COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    main()
