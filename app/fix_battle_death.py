import pathlib
import sys

# Make `app` importable when run as `python app/<script>.py` from the repo root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import os
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from app.admin_credentials import admin_settings, mysql_url

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, ".env")
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "data"))
DB_DIR = os.path.join(DATA_DIR, "spiderman", "databases", "battle_death")

load_dotenv(ENV_FILE)

# ADMIN tool (writes data): credentials come from MYSQL_ADMIN_USER /
# MYSQL_ADMIN_PASSWORD (or the legacy MYSQL_USER / MYSQL_PASSWORD) in app/.env.
engine = create_engine(mysql_url(admin_settings(), "battle_death"))

tables = [
    ("battle", ["id", "name", "date", "bulgarian_commander", "latin_commander", "result"]),
    ("ship", ["lost_in_battle", "id", "name", "tonnage", "ship_type", "location", "disposition_of_ship"]),
    ("death", ["caused_by_ship_id", "id", "note", "killed", "injured"]),
]

print("Loading battle_death data...")
print()

with engine.begin() as conn:
    conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))

for table_name, expected_columns in tables:
    csv_path = os.path.join(DB_DIR, "data", f"{table_name}.csv")

    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Missing CSV: {csv_path}")

    df = pd.read_csv(csv_path)

    actual_columns = list(df.columns)

    if actual_columns != expected_columns:
        raise ValueError(
            f"Column mismatch for {table_name}.\n"
            f"Expected: {expected_columns}\n"
            f"Found:    {actual_columns}"
        )

    print(f"{table_name}.csv")
    print(f"  CSV rows: {len(df)}")

    with engine.begin() as conn:
        existing = conn.execute(
            text(f"SELECT COUNT(*) FROM `{table_name}`")
        ).scalar()

    print(f"  Existing MySQL rows: {existing}")

    if existing > 0:
        print(f"  Skipping {table_name}: table is not empty.")
        print()
        continue

    df = df.where(pd.notna(df), None)

    df.to_sql(
        table_name,
        con=engine,
        if_exists="append",
        index=False,
        method="multi",
        chunksize=500,
    )

    with engine.begin() as conn:
        loaded = conn.execute(
            text(f"SELECT COUNT(*) FROM `{table_name}`")
        ).scalar()

    print(f"  Loaded MySQL rows: {loaded}")

    if loaded != len(df):
        raise RuntimeError(
            f"Row-count mismatch for {table_name}: "
            f"CSV={len(df)}, MySQL={loaded}"
        )

    print()

with engine.begin() as conn:
    conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

print("FINAL CHECK")
print("-" * 30)

with engine.begin() as conn:
    for table_name, _ in tables:
        count = conn.execute(
            text(f"SELECT COUNT(*) FROM `{table_name}`")
        ).scalar()
        print(f"{table_name:10s}: {count}")

print()
print("battle_death data loading completed successfully.")
