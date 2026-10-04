import pathlib
import sys

# Make `app` importable when run as `python app/<script>.py` from the repo root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, inspect

from app.admin_credentials import mysql_url, read_only_settings

db_name = "activity_1"

# Read-only: uses the SELECT-only account when MYSQL_RO_USER is set.
engine = create_engine(mysql_url(read_only_settings(), db_name))

inspector = inspect(engine)

tables = inspector.get_table_names()

for table in tables:
    print(f"\nTABLE: {table}")

    columns = inspector.get_columns(table)

    for column in columns:
        print(f"  {column['name']} : {column['type']}")